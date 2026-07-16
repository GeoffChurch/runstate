# Dead ends

Refuted ideas, with diagnosis. Parallel to `../backlog/`. The bar is not "things we
considered briefly and rejected"; it is "things we investigated seriously enough to
learn something, and now the *reason* they didn't work is worth preserving so we don't
re-tread the same ground." An entry arrives here when its backlog entry leaves
(`../README.md`); git carries the original proposal.

Each file keeps the same shape: what it proposed, why it died, the root error, what
survives, and where the live work went instead. The hooks below are the *reason*, not
the summary — enough to recognize the idea when it comes back wearing a new hat.

- [failure-detector](failure-detector.md) — **REFUTED 2026-06-24** (four-angle red-team).
  Observe-then-claim: heartbeat-staleness promoted to the *authoritative* cross-host
  episode-claim detector. Died because it aimed the **weakest** detector (staleness
  inference) at the **highest-stakes** decision (the claim), where being wrong is
  catastrophic *and irreversible*: double-live → divergent re-emission at the same step →
  `history()`/`ensure` raise **stickily and forever** on the append-only log, poisoning the
  very reuse-by-`run_id` case the effort validates. The "stuck" bug it fixed is merely
  recoverable. Read this before proposing any liveness-*inferred* claim gate: ◊P is the
  right floor for **observation** (cheap, revocable), while the **claim** needs
  *definitive* evidence — a record, a sound same-host probe, or a connection-oriented
  backend's lock (the path taken; composes with `../specs/channel-postgres.md`).
- [ensure-extend-pushorfail](ensure-extend-pushorfail.md) — **REFUTED 2026-06-25**
  (three-angle red-team, verified against the mycooc consumer). `extend(until)` inverted
  into a **push-or-fail** operator that swallows liveness — retrying recoverable deaths
  internally and raising otherwise — reducing `ensure` to fixpoint iteration. Died on the
  *same* sticky reuse-poisoning by a sequential route: it read the preempt/kill asymmetry
  as a "leak" to erase, when clean-final-checkpoint ⟺ no-overlap ⟺ safe-to-redrive is a
  **protective invariant** — erasing it writes the divergent re-emission — and liveness
  leaked back out through the handle, the reason, and the yield point regardless. The
  *diagnosis* survives (killed-redrive is worth **narrowing**, by making it safe rather
  than by erasing the distinction; recoverability is canonically derivable, not a workload
  opinion); the live path is the minimal predicate in
  `../backlog/ensure-redrive-recoverable-terminations.md`.
- [window-closed](window-closed.md) — **REFUTED 2026-07-11** (the 2026-07 review). A public
  `window_closed(progress, until)` for the half-open-window fencepost (`until={"step": N}`
  is `[0, N)`, so `progress + 1 >= N`). Died as **shallow arithmetic on an already-correct
  value**: every observable that earns its place traces to the append-only/multi-episode
  staleness the observer plane exists to hide, and this traces to nothing. The empirical
  kill — both consumers spelled it right by hand, and a footgun nobody trips is not a
  footgun (but see the file: *do not* generalize that move into "unused ⇒ sugar"). The
  beneficiaries needed the **rule**, not the API; it lives on `observables.progress`'s
  docstring. The 2026-07-16 postscript *sharpens* rather than reopens it: the real footgun
  is the fencepost's **placement** (two sites — pre-yield vs post-tick — diverging at
  `N = 0` and on resume-into-a-met-target), which the helper would not have caught, since
  both sites would have called it with different coordinates.
