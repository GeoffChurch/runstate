# The Store deliberation — decision record (living)

**Status:** in progress (2026-06-11). Q1 dissolved; Q2 (placement) DECIDED;
Q3 (membership / the experiment plane) and Q4 (what runstate ships) open.
Evidence base: [mycooc-adoption](mycooc-adoption.md) (the recon ledger) +
a 7-agent adversarial panel (3 advocates A/B/C, frame-breaker, prior-art
miner, consumer-grounding, cross-exam; dossier split at `/tmp/storeq1/`,
workflow run `wf_58b7f7b0-40c`). This file accumulates settled decisions
on the way to `docs/specs/store.md`; it is the spec's seed, not the spec.

## Q1 — "where does relational truth live?" — DISSOLVED per fact

The trichotomy (A authoritative Store / B derived index / C
experiment-as-run) mis-poses the question: the facts differ on the two
axes that discriminate — *re-verifiable at read?* × *must outlive the
run's channel?* — and each option is right for one fact, wrong as the
general answer. Settled per-fact homes:

- **rid → location:** not a stored fact — the channel's ADDRESS
  (placement; Q2). Residue: an owned **root set**, the irreducible
  authoritative kernel (prior art's floor: Iceberg/Delta converged from
  authoritative-store to facts-in-band + derived index + a catalog that
  only enumerates roots).
- **Provenance (analysis-of, …):** a backward edge on the CHILD's own
  log, written at creation — the git-parents shape; already half-shipped
  (`analysis_run_id` embeds the analyzed rid; the deterministic
  `analysis/{arid}.db` path makes forward dedup free). All forward maps
  (children-of, experiments-of) are computed, never stored.
- **"reused-from":** dissolves — under content addressing it is
  membership multiplicity (one rid, two experiments), not an edge.
- **Membership:** the contested fact — Q3.
- **cell → current-rid** (the hidden 4th fact, what `open_cell_channel`
  needs the marker for): the cell's pointer (Q2).
- **The config projection** (the hidden 5th): real queries are by config
  FIELDS and a rid is a hash — member records must carry a queryable cfg
  projection, and the which-config fork (orchestrator-merged vs
  worker-resolved) is decided by the write site, deliberately (Q3).

Adopted invariants, binding on every later choice:

- **Verify-at-use, inside the query helper:** the relational layer only
  LOCATES; terminal/progress/liveness are read from the channel at
  decision time. No store/index ever caches run state (under B′ they are
  episode-relative and mutable; wandb's false-"crashed" is the cautionary
  precedent).
- **Failure polarity:** every relational-layer failure must degrade to
  false-MISS (recompute) — never false-hit. The codebase already votes
  this way ("no channel — skip (safe: recompute)").
- **Facts are true-at-append observations** ("dispatched R into E at t"),
  never mutable states ("R lives at P", "R is complete") — every
  documented drift story is a state-fact aging out from under its store.
- **Write at dispatch** (the orthogonal dial, set once for all facts).
- **Any index is pure cache:** rebuildable, deletable, never
  authoritative; a CI rebuild-and-diff invariant guards against
  notmuch-style authority creep (one convenience fact written only to the
  index silently converts it).
- **The named A-residue, recorded not built:** scattered multi-host with
  no shared FS and no central channel backend — pure fleet-enumeration
  queries have no derived answer in that middle; it shrinks to nothing
  when the postgres channel backend lands (the index becomes a
  materialized view; an authoritative store would be redundant dual-write
  tables).

## Q2 — channel placement — DECIDED: content-addressed (option E)

Every run gets one neutral home named by its content —
`runs/<rid[:2]>/<rid>/` (channel + artifacts) — and experiment cells
become THIN CELL DIRS holding a pointer to the home plus cell-local
policy files (`.skip`, `.failure`). **Cell ≠ run is the load-bearing
distinction** (the recon's membership-as-placement gap was exactly their
conflation). Not a bare symlink: an operator's `touch cell/.skip` must
not skip the run for every claiming experiment.

What it buys (panel-verified mechanisms):

- **Reuse dissolves into `ensure`.** Dispatch = `ensure(rid,
  until=target)` against the one home: satisfied → pure log read; partial
  → EXTEND (the B′ payoff — the terminal-vs-partial reuse distinction
  evaporates); concurrent → the shipped birth-CAS arbitrates (one
  critical section across processes on sqlite) and the loser's `ensure`
  WAITS on the winner's log — wait/attach semantics with zero new code.
  `_find_reusable_run` / `_reuse_run` are deleted, not optimized.
- rid → location = path construction: nothing stored, stale, or rebuilt;
  survives the postgres-backend future verbatim.
- **Fixes a live custody bug:** today `_reuse_run` symlinks E1's physical
  `{rid}.db` into E2, so `rm -rf E1` destroys a run E2 depends on.
- The foreign-extend wart dissolves (every driver bakes `runs/<rid>`,
  never its own cell path).
- cell → rid = readlink; stale bindings (a cell satisfied under an OLD
  rid while the spec now implies a new one) become checkable.

Costs accepted, eyes open:

- The cell/run split ripples through every reader (mechanical; POSIX
  symlink traversal keeps file I/O mostly transparent; known leaks:
  tar/rsync need dereferencing, `du` under-reports, `os.walk` needs
  `followlinks`).
- **GC becomes required machinery:** mark-and-sweep — experiment pointers
  are the roots, `runs/` is the heap. It replaces `rm -rf`, which is the
  *broken* mechanism above; the full-tree walk measures ~26 ms today.
- Kernel false-hits get QUIETER: silent convergence on one run instead of
  visible duplicates — sharpens the own-your-`run_id`-partition burden
  (rate unchanged, detection harder).
- Loose runs (no rid computed) stay outside the namespace — coverage
  unchanged by any option.
- Cross-host without a shared FS: the gate is per-host (the named
  residue above).

Library scope ≈ zero: placement is policy over `open_channel(run_id,
root)`; runstate ships a recipe (root layout + shard scheme + the GC
sketch); the substrate is untouched. Sub-decisions taken with E:
cell-dir-with-pointer (not bare symlink); shard `rid[:2]`.

## Q3 — OPEN: the experiment plane + membership facts

(a) Does mycooc's runner become a real worker on an experiment channel?
Oracle evidence: the consumer already hand-rolled this in side files
(`.pid` with a stale-unlink race, the `.status` tmp+rename register,
`--stop` via SIGTERM, multi-invocation resume = undocumented episodes;
`run_state.py:100` calls the runner "the WORKER"). Invocations-as-episodes
is exactly B′ (dormant between invocations = preempted). "Experiment"
stays OUT of runstate vocabulary either way — it is just a run whose
worker is the dispatcher.

(b) Beyond the operational pointer, what carries membership? Candidates:
pointers-only + index over the tree (cheapest; loses order and
post-deletion history); member records on the experiment's channel at
dispatch (seq order, audit, rebind detection, the cfg projection's home,
"dispatched but unborn" crash narratives — and E structurally weakens the
custody objection: the experiment tree is now thin, so the natural prune
target is `runs/`, keeping experiment trees ~forever at KB–MB cost); the
tracked root (G — `summary.csv` already plays this role and 70/199
experiments survive ONLY there; killers: commit discipline and
consumer-shapedness). Note: with provenance on the child (H), live
derived work keeps its parent edges independent of any experiment's
lifetime — G's burden shrinks to historical enumeration.

## Q4 — OPEN: what runstate ships

Convention (schema-pinned `relation.*` records) vs user-dicts under
`topic="value"` — the CLAUDE.md helper test, entangled with whether the
index fold is a library observable or a consumer recipe; index as
component vs recipe; where the GC recipe lives. (Exam note: the proposed
record shapes do NOT pollute `value_series` — `_value_points` skips
records lacking a name / int step / `value` key — so the user-dict
spelling is viable; the question is recognizability, not safety.)

## Riders (parked, mycooc-side)

- `channel_read.py:28` doc bug ("written at dispatch" — false; the only
  marker writer is `_write_done_markers` at completion).
- The per-(variant, milestone-round) rescan bug around
  `run_experiment.py:3476`.
- The `.run_id` marker is a completion-time duplicate of the `{rid}.db`
  filename — under E it is deleted (pointer + filename carry the fact).
- `_complete_from_channel` stale-binding check (pointer rid ≠ freshly
  computed rid).
- Already folded into the ledger: `ensure` births `{rid}.db` at dispatch
  (`memoizer.py:200`); measured scan costs (~26 ms/walk).
