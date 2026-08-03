# Dead end: observe-then-claim (heartbeat-staleness as the authoritative claim detector)

**Status:** REFUTED 2026-06-24 by a four-angle red-team (theory / code / design / ops),
verified against the code. The *insight* survives; the *prescription* was unsafe and
over-scoped. The live work moves to the corrected path (see *Where it goes instead*).
Recorded here so the approach isn't re-attempted. The full original design is in git
history (was `../backlog/failure-detector.md`).

**Re-proposed standalone and re-refuted 2026-08-03.** The **per-tick supersession self-check**
below was proposed again on its own merits — not as observe-then-claim's bound — as a fix for #32.
Points 1 and 6 transferred verbatim and held; the other four do not transfer. Measurement and four
further refutations (it stops one step body too late, `retire()`/`stopped()` never detect, it breaks
`_lost`'s ordering invariant, and it grows every tick's read without bound) are in
`../specs/write-authority.md`. Read that before proposing it a third time.

## What it proposed

Recast "cross-host liveness for the claim gate" as: liveness is the **keystone failure
detector** runstate must own; the `lifecycle.heartbeat` is a backend-agnostic ◊P detector;
extract it as a stratified detector D (`peek_terminal` / `live_episode` / `Watcher` as rungs
`D0 ⊆ D1 ⊆ D2`); fix cross-host via **observe-then-claim** (a claimant watches the heartbeat
tip forward on its own clock and claims a silent foreign incumbent via the birth-CAS),
bounded by a **per-tick supersession self-check**; staged b → c-i → c-ii, with c-ii
re-layering the birth-CAS as consensus-on-D. Headline thesis: liveness is "the only
impediment to galaxy-scale over any atomic-append backend."

## Why it died

1. **Double-live permanently poisons reuse — strictly worse than the bug it fixes.**
   Observe-then-claim accepts a bounded window where two workers briefly co-run (a slow
   foreign worker wrongly suspected, claimed over, then revives). If they emit a divergent
   `value` at the same step (GPU non-determinism, a different BLAS path), `value_series`
   silently last-write-wins **and** `history()` raises `divergent re-emission … reuse would
   be unsound` — uncaught through every `ensure` path, and **sticky** (the pair is permanent
   on the append-only log → every future `ensure`/`history` raises forever). The "stuck" bug
   it fixes is recoverable; this is irreversible, and it lands on the reuse-by-`run_id` use
   case the whole effort validates. The supersession check bounds *process* liveness but not
   *log corruption* — the bad write lands in the same tick, before any check fires. (Verified
   empirically + against `memoizer.py` / `observables.py`.)

2. **The hazard is intrinsic to cross-host claim-on-inference, not just to this mechanism.**
   Single-host is safe because `os.kill` is accurate; cross-host you have only inference
   (staleness), which can be wrong → double-live. The conservative "stuck" behavior is a
   *correct* trade. Any claim-on-inference fix reopens it.

3. **NFS irony.** Observe-then-claim is a cross-host multi-claimant protocol — exactly what
   the shipped sqlite J2 caveat forbids (`single-writer-per-run is REQUIRED on NFS`). On the
   motivating deployment the CAS it leans on can admit two winners outright → guaranteed
   double-live.

4. **Staging dishonesty + a bug/capability conflation.** "(b) ships independently, birth-CAS
   untouched" contradicts "place observe-then-claim at `Worker.__init__`"; (b)'s safety leans
   on the c-ii consensus the doc calls the *upgrade*. And the *bug* ("the gate goes blind
   off-host") got conflated with a *new capability* (evicting foreign **live** incumbents on
   observation) that needs consensus to be safe.

5. **The headline theory claim is false.** "Liveness is the only impediment to galaxy-scale"
   — but a linearizable CAS is itself consensus-complete (Herlihy: CAS consensus number ∞),
   so at true galaxy-scale (no single home) the atomic append is a *second* irreducible
   primitive, and the dependency chain (liveness → consensus → … → ordered log) is circular;
   c-ii ("build consensus on D") is then redundant (single-home) or circular (multi-master).
   Citations were also off: FLP misattributed (the relevant result is "no perfect detector P
   in pure asynchrony"); the weakest detector for consensus is Ω not ◊S (and ◊P ⊋ ◊S, so the
   design *exceeds* the bar — the citation pointed the wrong way); "asynchronous" should be
   "partially synchronous" (◊P needs DLS-style eventual bounds).

6. **It didn't net-simplify, and it was scope-creep.** The "three liveness paths" are already
   one composed stack (one `resolve`, one `live_episode`, `peek_terminal` reused) — the
   "collapse" deletes ≈0 lines. The per-tick supersession check imposes a mandatory liveness
   burden on every worker (against "a worker composes its own loop"). Galaxy-scale is an
   explicit CLAUDE.md non-goal.

## The root error (one line)

**It used the *weakest* detector (heartbeat-staleness inference) for the *highest-stakes*
decision (the authoritative episode claim), where being wrong is catastrophic (double-live →
poisoned reuse).** Heartbeat ◊P is the right floor for *observation* (the `Watcher`, a UI —
being wrong is cheap and revocable); the *claim* gate needs *definitive* evidence — a record,
a sound same-host probe, or a connection-oriented backend's lock.

## What survives (genuine — keep)

- **Liveness is a failure detector, not truth** — every backend "liveness" (`os.kill`, an
  advisory-lock drop, a TTL) is a detector with an accuracy/latency profile.
- **The no-`Heartbeat.t` / no-substrate-lease vindications** — observer-local clocks are the
  clock-free, galaxy-scale-sound form; a sharper lease belongs in a connection-oriented
  backend, not the substrate. Reinforces shipped decisions (fold toward
  `../specs/lazy-launch.md`).
- **The player model** — nobody broadcasts a suspicion; the only shared liveness facts are
  records an actor wrote by *acting* (`stopped` / `terminated` / new `started`).
- **The heartbeat as the ◊P floor for *observation*** (not the claim) — already what the
  `Watcher` does.
- **The `resolve`-seam accelerator framing** — backend probes are evidence inputs to one
  detector; composes with channel-postgres.
- **The bug diagnosis is correct** — `live_episode` sits at the probe-only rung and goes
  blind off-host.

## Where it goes instead

The live backlog item ("Cross-host liveness for the claim gate") now points the **claim**
gate at a *definitive* cross-host oracle — a **connection-oriented backend's lock** (a
Postgres advisory lock) via the `resolve` seam (composes with channel-postgres) — **plus**
value-plane robustness (episode-scoped reads / a `history`/`ensure` that tolerates a residual
double-live without sticky-poisoning) so the never-zero ◊P window can't corrupt reuse.
sqlite-NFS stays conservative single-host (stuck-but-safe). The heartbeat detector keeps its
place as the observation floor.
