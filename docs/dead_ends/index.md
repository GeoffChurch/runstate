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
- [log-forking](log-forking.md) — **REFUTED 2026-08-04** (two prior-art sweeps + one mechanical hit).
  Resumption *copies* the log so a wrongly-resumed run cannot corrupt the incumbent's — claiming to
  convert the irreversible resume decision into a reversible one, and to dissolve double-live, the
  lineage problem and the episode epoch with it. Died because **it mistook the workspace for the
  fence**: no distributed log branches to arbitrate a suspected-dead writer ("a road that does not
  appear on the map"), and the versioned-data systems that *do* branch (Dolt, Iceberg, Datomic) all
  arbitrate with **CAS on a single mutable pointer** and branch only for isolation/audit — which means
  the field converged on exactly the mechanism runstate's birth CAS already is. Mechanically, forked
  autoincrement counters collide (Dolt's own `AUTO_INCREMENT` scar), and `seq` order is what every
  intro/eliminator pair is defined by. Read this before proposing any fork/branch/copy-the-log scheme
  — but note the split: **containment is a live goal**, and the workspace form survives in
  `../backlog/machine-partitioned-logs.md`.
- [per-episode-loglets](per-episode-loglets.md) — **REFUTED 2026-08-05** (measured). One loglet per
  episode, with `(episode, seq)` as a total order: claimed to dodge the partial-order gate, to supply
  the fencing asymmetry runstate lacks, and to fix the value plane structurally. Died on a **dilemma
  with only two arms**: with per-segment `seq` it *destroys the birth CAS* (measured — two workers
  each win a claim, because a CAS arbitrates only writers sharing a frontier, and
  `PRIMARY KEY (run_id, seq)` **is** the arbiter); with global `seq` and `segment` as a label,
  `open_segment()` allocates nothing `latest_episode().seq` does not, so it is the birth CAS
  relabelled. The fenced variant is *worse than today* — a seizure mutes the live worker while adding
  **zero records**, where a forged claim at least leaves a readable one. Kafka's `InitProducerId` is
  safe because a **broker** issues the epoch; renaming an append does not create a broker. Read before
  proposing any segment/loglet/sub-log scheme — and note the two keepers: a total order that
  *disagrees with append order* is worse than an honestly partial one, and the real defect it found is
  fixed by **correlation, not segmentation** (`../backlog/episode-correlation.md`).

