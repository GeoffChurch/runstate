# mycooc adoption + upstream candidates

**Status:** ledger, updated 2026-06-11 from a recon of post-rebase mycooc.
The original 2026-05 assessment said "a full rebase isn't possible today" —
that is now false: mycooc rebased onto runstate end-to-end across the June
arc (worker emits lifecycle/B′; the channel is the run's complete external
interface; ensure-driven milestone dispatch; derived analysis runs). This
doc now does two jobs: (1) settle the May claims against what shipped, and
(2) carry the **store-shaped recon facts** that feed the Store deliberation
— the one big deferred piece left.

## Ledger: the May claims, settled

- **"Full rebase isn't possible today" — false now.** Phases 1–6b executed:
  the worker mirrors every metric onto the channel (`channel_read.py` is the
  single read seam), `.state`/PID files are gone (lifecycle + handle
  liveness), dispatch is `ensure` over a milestone ladder via a producer
  wrapping `LocalLauncher`, and the control inversion (friction 6) was
  resolved in phase 6b.
- **"Completed-but-extendable" (friction 4) — SHIPPED** as run-episodes B′
  (`specs/preempted-vs-completed.md`, stop-discharge): a `stopped` ends an
  episode, not the run; `ensure`/extend resumes a completed run.
- **"Idempotent relaunch" (friction 5) — SHIPPED** as the birth-CAS +
  `relaunch_if_needed` + `ensure_served` (`specs/lazy-launch.md`).
- **The `run_id()` recipe — SHIPPED** (`specs/run-id-recipe.md`); mycooc's
  `run_id.py` implements it (config + git-state-by-content + data files),
  and derived-run identity composes on top (`analysis_run_id`,
  `specs/derived-runs.md`).
- **reuse-by-hash — half-shipped, app-side only.** mycooc now computes the
  rid at dispatch and runs `_find_reusable_run` + `_reuse_run` (symlink
  `metrics_global.csv` + `checkpoint_best.pt`, write `.reused_from`). What
  it lacks is exactly the relational layer — see the recon below.
- **`stopped.reason` vocabulary recipe — still parked** (small; rides with
  any future convention-docs pass).
- **Per-run `runstate status` CLI — still backlog** ([cli-status](cli-status.md));
  the *matrix* status stays app-side, as judged in May.

## The store-shaped recon (2026-06-11)

What mycooc actually has today where a Store would sit — file:line refs into
`~/src/mycooc/run_experiment.py` unless noted:

- **Scale:** 130 experiment dirs, 2052 cell dirs (`outputs/experiments/
  <exp>/<scenario>/<variant>/`). Only **3** `.run_id` marker files exist
  (all in `_harness_fixture`) and **0** `.reused_from` — the identity index
  over real history is empty, and reuse has never fired in anger.
- **Identity IS persisted at dispatch — as the channel filename — but the
  reuse query reads a completion-time duplicate.** `ensure` births
  `{rid}.db` on entry (`runstate/memoizer.py:200` opens `producer.channel`
  before the first launch), so the rid is durably on disk from the moment
  of dispatch. `_find_reusable_run`, however, keys on the `.run_id`
  side-file, written only by `_write_done_markers` (`:705`) at
  completion-classification time. Two consequences: history predating June
  is unindexed forever (unless backfilled), and a **partial-but-extendable**
  run — the thing B′ made valuable — is invisible to the reuse query even
  though its channel exists from dispatch and `_find_reusable_run` takes a
  `min_steps` a partial could satisfy. (`channel_read.py:28`'s claim that
  the marker is "written at dispatch" is a verified mycooc doc bug.)
- **The reuse query is a full scan — but the cost is a consumer bug, not
  a scale wall.** `_find_reusable_run` (`:414`) walks all
  `outputs/experiments/*/*/*` dirs, reads each `.run_id`, requires
  `{rid}.db` + `_reusable_from_channel(channel, min_steps)`. It sits
  inside the (variant × milestone-round) loop (`:3476`), so a chunked
  24-cell experiment approaches ~240 full walks per invocation. Measured
  on the real tree, though: one full walk = ~0.026s warm; a full
  open+fold of a channel = ~0.14ms — so the whole pathology is seconds
  today, fixable with one walk per invocation under any design.
- **Membership IS directory placement.** Run × Experiment is one-to-many by
  construction (a cell lives at one path); the many-to-many case (same
  content in two experiments) is approximated by a second dir holding
  symlinks + a `.reused_from` path marker (`:478`) — an artifact-level
  copy, not a membership fact. There is also a *legacy second identity*
  (`.config_hash`, kept for the YAML `reuse_from` path, `:3472`).
- **The status matrix never needs enumeration.** `show_status_vertical`
  (`:2504`) walks the *spec's* Cartesian grid (scenarios × variants ×
  seeds) and per cell reads the channel (`_complete_from_channel`,
  `_channel_phase`, liveness, progress). The grid's authority is
  `experiment.yaml`; the channel is the authority for per-cell state. A
  Store is not needed to answer "what cells does this experiment have" —
  only cross-experiment/cross-run queries lack a home.
- **Provenance is split across two roots:** tracked `experiments/<name>/`
  (summary.csv, git_state, restricted_eval, the YAML, results.md —
  `run_experiment.py:194`) vs gitignored `outputs/experiments/<name>/`
  (channels, checkpoints, per-step CSVs).
- **New relational citizens since May:** derived analysis runs (channels
  under `{run_dir}/analysis/`, identity `analysis_run_id(analyzed_rid,
  read_set, params, code)` — provenance edges rid → derived-rid currently
  discoverable only by globbing the subdir), and service runs (leased
  demand) on the runstate side.

**The queries that still have no home** (the Store's burden of proof):
1. rid → location/channel ("does this content exist anywhere?") — today a
   full scan over marker files that mostly don't exist.
2. rid ↔ experiment membership, many-to-many — today unrepresentable;
   symlink piles approximate it.
3. rid → derived rids (and `.reused_from`-style edges) — today filesystem
   archaeology.

## Upstream candidates (what's left)

1. **The Store** — the one big deferred piece (design §14). The recon above
   is its requirements doc; the central design fork (authoritative second
   source of truth vs derived rebuildable index over the channels) is the
   Store deliberation's opening question. Keep *artifact* sharing (mycooc's
   symlinks) out of scope — runstate transports messages, not files.
2. **`lifecycle.stopped.reason` vocabulary recipe** — unchanged from May;
   an opt-in documented vocabulary, never an `outcome` enum expansion.
3. **Per-run `runstate status <run_id>` CLI** ([cli-status](cli-status.md)).

Stays in mycooc (orchestration policy / workload-specific): the smoke gate,
the no-progress guard, the retry/resume policy table, `--diff`/Cohen's d,
Hydra config, checkpoint mechanics, the matrix/ETA/seed-aggregation display.
