# Spec: lazy-launch (`ensure_served` — the leased-demand waker)

**Status:** SHIPPED 2026-06-11 (pipeline: adversarial round REFUTED the
draft and all amendments folded; consistency sweep's 15 fold-backs landed —
including the vacuous-green fixture finding, fixed atomically with the
`resolve` change; implemented same day: `launcher.ensure_served` + `reap()` +
the reap discipline, hostname-scoped `resolve`, the `stopped()` loser guard,
`examples/monitor/` running the full loop twice). Originally: stage 1 cleared
2026-06-11 — the adversarial round
REFUTED the draft (three killers: the ThreadLauncher degeneracy, the
null-worker verdict deletion, the zombie-factory recipe) and all amendments
are folded below; the three deliberated confirmations stand (decider shape;
wasted-spawn posture + the reap discipline, reformulated; polling). Completes the service story end to end
(`specs/service-worker.md` + `specs/time-lease-boundary.md` provide its two
inputs: the demand fold, and the deletion of all flap policy). Closes design
§12.1.

## The model

> **Two demand durabilities, two deciders.** `relaunch_if_needed` already
> serves the *durable* path (the launch contract / `ensure`'s target:
> relaunch iff no live episode). `ensure_served` is its **leased-demand
> sibling**: wake iff there is *live leased demand* and no live episode —
> the same composition with one more gate, and the gate is the public fold:
>
> ```python
> def ensure_served(launcher, run_id, target, **launch_kwargs):
>     channel = launcher.open_channel(run_id)
>     if not live_demand(channel):          # leased demand, boundary-aware
>         return None
>     if live_episode(channel) is not None: # someone already serving
>         return None
>     return launcher.launch(run_id, target, **launch_kwargs)
> ```
>
> Returns the new `LaunchHandle`, or `None` (nothing needed — no demand, or
> already served; callers who must distinguish read the folds themselves).
> The returned handle is for `wait`/reap convenience — **never
> `Watcher.add()` it** (it may be a claim-race loser, and the probe tier
> would verdict a healthily-served run `presumed_dead`); track lazily-woken
> runs with `observe()`.

**Decider scope (single-host honesty):** the liveness gate resolves handles.
`resolve()` is hostname-scoped (a `local://` handle for *another* host — or
any foreign scheme — returns None = not locally resolvable), and
`live_episode` treats unresolvable as live, so `ensure_served` is
**conservative off-host: it never wakes a run whose last episode it cannot
probe.** A cross-host/cluster decider needs a staleness-based liveness tier
this spec does not provide — recorded, not built. Two more named edges:
between `launch` and the child's claim, `peek_terminal` still shows the
previous episode's terminal (don't gate driver logic on it across a wake);
and waking a run that had *completed* demotes its latest verdict to the new
episode's outcome — model-inherent in any relaunch, automated here, so
`ensure_served` is for service runs, not completed autonomous ones.

**Caller-invoked primitive; daemon as composition (confirmation 1).** The
demander calls it — subscribe, then `ensure_served` — because in every
validated consumer (the monitor driver; mycooc-analyze) the demander and the
operator are the same party, and a one-shot helper needs zero standing
infrastructure. The launch recipe is **plain data** (`target` +
`launch_kwargs`, exactly `relaunch_if_needed`'s contract — never a callback
or subclass), so the standing-daemon form is a *composition*, not a second
implementation:

```python
with LocalLauncher(root=root) as launcher:
    while True:                              # the activator recipe
        launcher.reap()                      # MANDATORY: see below
        for run_id, target, kw in table:
            ensure_served(launcher, run_id, target, **kw)
        sleep(poll_interval)
```

(Under `specs/store.md` Recipe-1 placement — per-rid content-addressed
roots — the one-launcher-many-rids shape above needs a per-rid wrapper
loop: construct the launcher, or at least its root, per run_id.)

The per-cycle `launcher.reap()` (a new public method: poll every
outstanding handle) is load-bearing, not hygiene: a never-exiting loop
never hits `__exit__`'s reap, so finished children accumulate as POSIX
zombies — and `os.kill(pid, 0)` **succeeds on a zombie**, so a crashed
service would read live to `live_episode` forever and the daemon would
never re-wake the one run it exists to revive. Reaping frees the pid;
`resolve()` then answers honestly.

Honest counterargument, recorded: real socket activation (systemd) puts the
launch config in a resident daemon precisely so *clients stay
launch-ignorant* — the knowledge asymmetry services exist for. That
asymmetry has no instance here yet; **the promotion trigger is named**: the
day a launch-ignorant demander exists (the viewer thread), the daemon recipe
graduates to a shipped component holding the table. Re-waking after a
later death is also the demander's job (its presence is already the
keepalive — renewals — so its presence is also the waker: no values + no
live episode on its own read cycle ⟹ call `ensure_served` again); a daemon
improves wake *latency*, never semantics.

**Wasted spawns accepted; corpses disciplined (confirmation 2).** No
coordination between wakers: the worker's birth-CAS arbitrates every
double-spawn — the loser exits before acting. The deliberation found the
old "wasted spawns are cheap" analysis priced the spawn and forgot the
funeral, twice:

1. **The corpse is recorded honestly; it just cannot speak (SUPERSEDED
   2026-07-14 by `specs/launcher-record-identity.md`).** A reaping launcher
   records what its own child did — *unconditionally* — and the launch's
   correlation id says whose death it is. A claim-race loser's clean exit
   lands on the log as exactly that (a launch that ended, having never
   claimed), and `peek_terminal` ignores it structurally: the launcher tier
   is anchored to the **claimed** episode and pairs by id, so only the death
   of the launch that the claim answered can speak for the run.
   *This section previously specified a **reap discipline** — a
   foreign-claim-scoped, `launched`-seq-scoped conditional SILENCE in
   `_reap`, suppressing the loser's record so it could not be misread as the
   run's `completed`. That was a writer-side workaround for identity-less
   records, and it is **deleted**: with identity, the writer stays honest and
   attribution is the reader's job. Its own "acknowledged residue" (an
   unclean loser polluting the verdict plane with `errored`, "curable only by
   per-handle pairing — the `terminated`-identity schema bump already filed")
   is cured too: that bump shipped as launcher-v0.3.*
   **Scope:** the multi-waker posture no longer needs log-distinguishable
   *child* identity, because identity now lives on the *launch* rather than
   the handle — so `ThreadLauncher`'s shared `local://host/pid` is no longer
   disqualifying, and concurrent dispatch over it no longer forges verdicts.
   (`ThreadLauncher` remains unable to force-terminate a thread; that is a
   separate, intrinsic degeneracy.)
2. **The loser may not speak (the `stopped()` hole).** "A loser never acts
   on the channel" was enforced everywhere except an *explicit*
   `Worker.stopped()` call — the minimal example's own
   `w.stopped(completed=True)` would, in a double-spawn, write a `completed`
   claim onto the winner's live log (discharging its pending stops and
   terminating the record-based verdict). `stopped()` gains the `_lost`
   guard, pinned by a test.

Prologue guidance, sharpened: claim **before allocating anything that could
hurt the winner** (the loser of a race must not OOM the victor off the GPU)
— documentation, not mechanism.

**Polling (confirmation 3).** The decider reads the log; on sqlite that is a
poll (the demander's natural read cadence, or the daemon's `poll_interval`).
Push-wake (LISTEN/NOTIFY) rides the channel-postgres backend whenever that
lands — an optimization slot, no semantic change. No flap policy exists
anywhere (`specs/time-lease-boundary.md` bounded the ghost at ≤2 relaunches
by construction; the one surviving constraint: no `ensure` over stepless
services).

## Non-goals

- A service registry / launch-config store (the table is the daemon
  operator's; the primitive takes data).
- A shipped daemon (recipe only, until the named promotion trigger).
- Any waker-side relaunch policy (deleted upstream).
- `terminated` identity / per-launch pairing — *filed here as out-of-scope;
  SHIPPED 2026-07-14 as launcher-v0.3 (`specs/launcher-record-identity.md`),
  which superseded this spec's reap discipline (see the corpse note above).*

## Deliverables

`launcher.py`: `ensure_served` beside `relaunch_if_needed`; a public
`LocalLauncher.reap()`. *(The foreign-claim-scoped reap discipline this spec
shipped in `_reap` was deleted 2026-07-14 — see the corpse note above.)*
`vocabulary/handle.py`: `resolve()` becomes hostname-scoped (a pre-existing
bug the review surfaced: it probed the *local* pid table for any host's
handle — false-dead off-host would double-claim; foreign-host handles now
return None → staleness tier). `worker.py`: the `_lost` guard on
`stopped()`.
`examples/monitor/driver.py`: the full demand loop — subscribe,
`ensure_served` (replacing the manual launch), serve, lapse, retire,
re-subscribe, **re-wake** — the end-to-end dogfood of the whole service
story. Docs: design §12.1 → closed (this spec); trackers; the activator
recipe documented here only.

## Tests (TDD targets; the backends where applicable)

*(2026-07-16 note: the reap-discipline rows below describe the DELETED
mechanism — `launcher-record-identity.md` (2026-07-14) superseded it, per the
corpse note above. The shipped pins now assert the opposite surface: the
loser's corpse IS recorded and speaks for nobody — see
`tests/test_service_worker.py` and `tests/test_thread_launcher.py`.)*

- `ensure_served`: launches when demand ∧ no live episode; `None` when no
  demand (even with no episode); `None` when already served (live episode);
  demand that is boundary-voided or answered does not wake (it consumes
  `live_demand`, so the lease semantics ride along free).
- Double-waker race (LocalLauncher): two concurrent `ensure_served` calls →
  exactly one winning episode; the loser exits clean; **no `terminated`
  record for the foreign-claimed-away loser**; the winner unconfused.
- Reap discipline: clean-exit loser with a foreign claim → no record;
  clean-exit child with NO claim at all (the null worker) → record kept
  (its only terminal); nonzero-exit unclaimed child → `terminated{exited,
  rc}` kept; claimed child → record as today; a pre-existing same-pid
  `started` from an OLD episode does not count as this child's claim (the
  launched-seq scope).
- `resolve()` hostname scoping: another host's `local://` handle → None
  (not False); the local host's dead pid → False as today.
- `LocalLauncher.reap()`: a finished child is reaped without `wait()`/exit;
  zombies don't accumulate; post-reap, `resolve()` reads the pid dead.
- `stopped()` loser-guard: a lost worker's explicit
  `stopped(completed=True)` writes nothing.
- `examples/monitor` smoke: demand → wake → serve → lapse → retire →
  re-demand → **re-wake** (a second episode on the same run), terminal and
  `live_demand` read back correctly.
