# mycooc adoption + upstream candidates

**Status:** assessment (2026-05), not work-in-progress. `~/src/mycooc` is an
existing ML experiment framework that predates and partly inspired runstate;
it's the **validating use case** for the deferred relational layer (Store +
Hasher + reuse). This doc records what adopting runstate would take and what
should flow *upstream* into runstate.

## Bottom line

mycooc independently re-invented ~70% of runstate's protocol (`run_state.py`
mirrors the lifecycle convention; its `.preempt {"at_step": N}` is a
one-condition prototype of `control.stop` + the `from`-algebra). **A full rebase
isn't possible today** — the two pieces mycooc leans on hardest, **reuse-by-hash**
and the **`scenario × variant × seed` relational identity/membership**, are
both the **Store** runstate deferred to Layer 4 (design §14) — the "Hasher"
turns out to be a `run_id()` recipe, not a component (see [index](index.md)). The
**control/lifecycle/liveness plane maps cleanly and is an *upgrade*** (runstate's
condition-algebra + handle-liveness > mycooc's `.preempt` file + PID file). So:
rebase the control plane now, keep store/hasher/status app-side, migrate them
when Layer 4 lands — using mycooc's code as the reference.

## Rebase today? — partial

- **Maps cleanly (upgrade):** cooperative preempt → `control.stop {}`; deferred
  `{"at_step":N}` → `control.stop {"from":{"step":N}}` (plus `any`/`all`/time/count
  for free); PID liveness → handle probe + heartbeat staleness; per-run `.state`
  → `lifecycle.*`; sequential dispatch → `sweep`.
- **Blocked (no runstate primitive yet):** reuse-by-hash; the `--status` *matrix*
  (grid counts + ETA + cross-seed aggregation); Cartesian config generation;
  "completed-but-extendable" resume (→ see [run-episodes](run-episodes.md)).

## Upstream candidates (highest value first)

1. **Store + reuse-by-hash** — *the real Layer-4 component.* A **separate
   Protocol**, NOT a channel convention: reuse-by-hash and the
   `scenario × variant × seed` identity are cross-run relational queries the
   per-run topic log structurally can't answer. mycooc proves the many-to-many
   **Run × Experiment** table is non-optional. Keep *artifact*-sharing (mycooc
   symlinks checkpoints) out of scope — runstate transports messages, not files.
2. **A `run_id()` recipe (the ex-"Hasher")** — *a recipe, not a Protocol.* A
   hasher's only content is the choice of which inputs determine the run's
   output (workload-specific → user code); and the substrate already affords
   content-addressable identity (set `run_id = h(inputs)`; reuse-for-dedup =
   `open_channel` + `peek_terminal`). Ship one reference `run_id()`: mycooc's
   `_compute_config_hash` + `_compute_git_fingerprint` is the seed, refined to
   hash git state **by content** (don't infer clean-vs-dirty), which drops the
   `_fingerprints_compatible` repair predicate.
3. **A `lifecycle.stopped.reason` vocabulary recipe** — mycooc distinguishes
   `patience` / `max_steps` / `preempted` and resumable-`timed_out` vs
   fatal-`crashed`. Don't expand the closed `outcome` enum (that bakes policy —
   see `liveness.py`); ship an *opt-in documented vocabulary* instead.
4. **Per-run `runstate status <run_id>` CLI** ([cli-status](cli-status.md)) — clean.
   The *matrix*/ETA/seed-aggregation stays app-level (workload-specific).

Stays in mycooc (orchestration policy / workload-specific): the smoke gate, the
no-progress guard, the retry/resume policy table, `--diff`/Cohen's d, Hydra
config, checkpoint mechanics.

## Friction points (biggest first)

1. **"runstate transports one run; mycooc manages a matrix."** The no-Experiment-
   class / one-`run_id`-one-channel / sequential-`sweep` stance (design §9, §14)
   collides with mycooc's raison d'être (a Cartesian grid with reuse/ETA/resume).
   That management layer is what runstate calls "likely never core."
2. **Run identity:** positional `(exp, scenario, variant, seed)`→dir vs an opaque
   `run_id`. The mapping needs the Store to live somewhere.
3. **Artifact reuse is shared-FS symlinks** — won't follow into a message
   substrate (esp. a NATS-style backend).
4. **"Completed-but-extendable"** — dissolved by the [run-episodes](run-episodes.md)
   model (a `stopped` ends an episode, not the run), but that's unbuilt.
5. **Idempotent relaunch** — mycooc's "re-run = no-op if alive" PID-guard maps to
   the lazy-launch single-spawn guard (design §12.1), unbuilt; see
   [run-episodes](run-episodes.md).
6. **Control inversion** — `Worker.tick()` drives the loop; mycooc's worker is a
   guest callback inside `aligner.align(...)`. Doable (the callback already is the
   safe point), but an integration seam.

## What this drives in the backlog

- [run-episodes](run-episodes.md) — the extendable-terminal + idempotent-relaunch
  half.
- **The Store + a `run_id()` recipe** (index.md, "relational layer") — the dedup
  + enumeration half; mycooc is the reference and the validating use case.
- A `lifecycle.stopped.reason` vocabulary recipe (new; small).
