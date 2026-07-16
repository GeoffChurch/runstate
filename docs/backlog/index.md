# Backlog

Forward-looking ideas for runstate. One file per idea where it warrants
elaboration; smaller items can live inline here under each section.

This index is a **snapshot of open work, not a changelog**: when an idea is
executed, delete its file or remove its inline entry (the shipped design lives in
`docs/specs/`; git carries the history). Refuted ideas (with diagnosis) move to
`docs/dead_ends/`, parallel to this directory.

## Start here (taking on the upcoming work)

A fresh session should read, in order: **`../../CLAUDE.md`** (orientation + the
shipped-vs-deferred scope snapshot) → **`../design-v0.2.md`** (the converged
design; §12 = open implementation items, §14 = scope) → **this file** (the
discoverable work list). For the v0.3 thread, also read the shipped specs
**`../specs/run-episodes.md`**, **`../specs/run-id-recipe.md`**,
**`../specs/memoizer.md`**, and **`../specs/store.md`** (the relational layer,
dissolved into recipes over the existing basis; decision trail
[store-deliberation](store-deliberation.md)), and the trail
**`../design-v0.3-exploration.md`**.
For the **dependency/synergy view** over this list — the items grouped into
clusters that unlock each other, with a sequencing — see
[synergy-map](synergy-map.md).

## Next pickups

- **[third-party-observer](third-party-observer.md)** — [LIVING · opened
  2026-07-14, the review's stage 6 executed adversarially] **the log records what a
  run DID, but not WHEN it did it nor WHAT IT WAS ASKED TO DO** — and neither
  absence is visible to the party that launched the run. The persona that falls into
  both (a viewer/TUI/scheduler attaching to a run it did not start) is new, and it
  is the one the TUI/viz work depends on. Six items + the ship order; each graduates
  to its own spec. **Item 1 (the observer clock) SHIPPED 2026-07-16**
  (`../specs/observer-clock.md`): the beacon is dated (`lifecycle`/`launcher`-`v0.4`),
  so the headline wrong-verdict (a run dead 21 days reading
  `Running(beacon_age=9.5e-06)`) is fixed; migrating existing dbs is owner-run
  (`scripts/migrate_observer_clock_v0_4.py`, the launcher-v0.3 precedent). **Next
  implementable pickup: item 2 — the run's TARGET** (`control.target`), the one
  *missing basis vector*, which awaits its own spec + adversarial pass (it may subsume
  conditional-stop, so it gets the full treatment). Then items 3–6 (enumeration /
  read-only open / cursored folds / third-party-stop safety) as the build demands them.
- **[ensure-await-completion](ensure-await-completion.md)** —
  `ensure(await_complete=True)`: gate on the producer's `completed` verdict, not the
  step/time window — for a consumer that depends on a post-terminal off-channel
  artifact (the two-channel-publish race). ~8 lines in `memoizer.py`, **no wire
  change**; consults the verdict plane already wired in. Rejects the contrast case
  (a `{completed:true}` *condition-algebra* term — wrong plane). Surfaced by the
  translation dogfood; **not urgent** (1 repo, 2 instances, 1 already mitigated) —
  promote when a live consumer depends on a post-terminal artifact.
- **[wal-liveness-mtime](wal-liveness-mtime.md)** — SUBSUMED 2026-07-14 (and
  re-graded from "minor") by [third-party-observer](third-party-observer.md)
  item 1; the runstate-side fix is now the `last_activity` fold in
  [observer-clock](../specs/observer-clock.md). Kept as the record of the
  WAL/mtime measurement (306 s stale on a healthy run) and the consumer-side
  sidecar-max fix.
- **[conventions-hygiene](conventions-hygiene.md)** — only the pid `?start=`
  disambiguator (F9) remains, deferred (rationale in the file); the rest of the
  adversarial orthonormal-basis audit of the L2 conventions resolved, and the basis
  audited as largely tight.
- **[release-and-stability-contract](release-and-stability-contract.md)** —
  [PROPOSED 2026-07-16, owner-gated] the release *mechanics* are prepared
  (packaging metadata, `MANIFEST.in`, a tag-gated PyPI trusted-publishing
  workflow, `CHANGELOG.md`) but every *policy* is open: what freezes at first
  release + what SemVer means against per-convention wire versions; the public
  form of the migration doctrine (retain scripts once strangers hold logs); the
  end-of-schema-file-deletion trigger; deprecation policy; the version-naming
  tension (`0.2.0.dev0` vs the docs' "v0.3" vs wire-`v0.4`); and the schemas-
  not-in-the-wheel question. Six decision points, each with a recommendation;
  nothing ruled. Blocks the first `pip install runstate`.

## Long-term ambition

- [visualization-story](visualization-story.md) — a **separate project** on top of
  runstate owns the data-plane / viewer protocols (richer event types: Histogram,
  Image, Tensor; viewer-discovery; artifact-storage) + a companion webapp/TUI —
  closing the data-plane gap without bloating runstate's core. These protocols do
  **not** live in runstate; they depend on it. Discovery rides the dissolved
  relational layer (`../specs/store.md`: the root set + pointers + the dormant
  index). Revisit when a viewer audience exists.

## Protocol extensions (control plane)

- **completion-reason register recipe — SHIPPED 2026-07-11** as a section in
  `../specs/completed-opt-in.md` ("Recipe: the completion-reason register"): a
  value-plane register carrying *why* a run stopped, blessing the SHAPE only (no
  vocabulary — workload words never enter the protocol) + the two safety rules
  the mycooc adoption's scars taught (episode-scope the read; the terminal owns
  done-ness, the register owns only why — never trust a pre-terminal register for
  done-ness). Defends the closed `outcome` enum from the recurring
  completion-classification bug class (mycooc 1963732, 8ea82e3). `RunResult.reason`
  stays branched-on-by-nobody by design (a refinement of `outcome`, not a why).
- **Cross-host liveness for the claim gate** — elaborated 2026-07-16 into
  [cross-host-claim-gate](cross-host-claim-gate.md) (DESIGN DELIBERATION, NOT
  CONVERGED, owner-gated: the two candidate designs analyzed against the
  invariants each touches, plus the owner-decision list). Summary:
  `live_episode` sits at the probe-only
  rung, so on a foreign host it goes blind and treats an unresolvable handle as live
  *forever*; a crashed foreign episode blocks the waker and the birth-CAS. The
  observe-then-claim / heartbeat-as-claim-detector approach is **refuted**
  (`../dead_ends/failure-detector.md`): claiming on heartbeat-staleness *inference*
  admits a double-live window that permanently poisons reuse, and the CAS it leans
  on is unreliable on the motivating NFS deployment. Sound path: the **claim** gate
  needs a *definitive* cross-host oracle — a connection-oriented backend's lock (the
  Postgres advisory lock now **ships** in channel-postgres, but as a *Watcher* signal; the
  deferred *co-arbiter* wires it into the claim gate) via the `resolve` seam — **plus**
  value-plane robustness
  ([value-plane-divergence-resolution](value-plane-divergence-resolution.md)) so a
  residual double-live can't sticky-poison the log. sqlite-NFS stays conservative
  single-host; the heartbeat ◊P detector remains the floor for *observation* (the
  `Watcher`), not the claim.
- **Watcher boundary-aware re-broadcast** — a time-keyed `broadcast` barrier
  subscription on a run that *resumes* is boundary-voided with no record
  (`../specs/time-lease-boundary.md`); today's steering is "spell barriers
  step-keyed" (design §9). A Watcher that watches `started`s and re-issues its
  broadcast across boundaries would lift the restriction. Small.
- **Time-axis unification** — three time anchorings now coexist by design:
  `history()` replays time atoms run-epoch-anchored; live subscriptions are
  episode-scoped (`time-lease-boundary`); stops re-anchor per episode
  (stop-discharge's note). Each is locally right; if a consumer ever needs them to
  agree, unify deliberately rather than ad hoc.
- **Discharge-by-id (merge-tolerant control folds)** — generalize the stop-discharge
  fold's positional rule ("pending until the *next* `lifecycle.stopped`",
  `../specs/stop-discharge.md`) — now two positional rules (the stop discharge AND
  the subscribe answer fold, design §7) — to explicit causal reference: a `stopped`
  names the `request_id`(s) it discharges. Makes the control fold commutative, so it
  survives multi-writer `control.*` (§12.7–8) and replicated logs, where "next" is
  not well-defined. No-op while every log has a single home — including the shipped
  single-instance Postgres backend (one total order, even under multi-writer `control.*`);
  revisit only with a replicated / multi-home log.
- [launcher-protocol-typing](launcher-protocol-typing.md) — the `Launcher` Protocol's
  `launch` can't be structurally typed (the two reference launchers have disjoint
  `launch` signatures: a callable `target` vs a `cmd`). Split the uniform
  `open_channel` from the per-launcher `launch` (helpers take a launch thunk). Interim:
  `launcher: Any` in the four helpers.
- **protocol-async-api** (inline; no file) — wrap the JSON Schema in AsyncAPI for
  a richer spec format (multi-channel, lifecycle events). Defer until the v0.2
  protocol grows enough to justify the layer.
- **Reconfigure command** — typed orchestrator-to-worker command for mid-flight
  hyperparameter changes. Dropped from v0.1 as speculative; add when at least 3
  concrete validated use cases exist.
- **Pause / Resume commands** — for cluster-preemption or "another job needs the GPU"
  scenarios. v0.2 if a real use case emerges; for now Stop + restart externally is
  the recommended pattern. *Prior art (Bluesky RunEngine, researched 2026-07-10):*
  deferred-pause-at-checkpoint independently validates the stop-at-next-safe-point
  level; the four-way post-pause fan-out (resume / stop / abort / halt) is a
  field-tested answer space for "paused, now what"; and **suspenders**
  (auto-suspend/auto-resume) need *non-monotone* band predicates — weigh that
  deliberately before extending the deliberately-monotone condition-algebra.
  (Bluesky's `RE.stop()` bakes `exit_status='success'` — the producer-baked bool
  runstate refuses; our no-`success` stance is the more principled projection.)
- **Snapshot command** — orchestrator asks worker to checkpoint without exiting.
  Workload-specific; users can define their own dict shape via raw Channel. Promote
  to first-class if a common shape emerges.
- **Cleanup / resource-pressure commands** — `Cleanup(level: str)` for "GPU memory
  pressure, please free buffers". Too workload-specific to standardize; leave to
  user-defined dicts.

## Backends

- **channel-postgres — SHIPPED** (`../specs/channel-postgres.md`): the cross-host backend.
  Claim = the uniform shared-log CAS (cross-host single-spawn + control fall out of it);
  liveness = the advisory lock as a Watcher-consumed signal, *not* a claim arbiter. Still
  open (deferred in the spec): low-latency push (LISTEN/NOTIFY), cross-host auto-relaunch
  (the rejected co-arbiter — see "Cross-host liveness for the claim gate" above), sharding,
  HA.
- **channel-redis** — a Redis backend; an alternative to Postgres for cross-host scenarios
  (lighter daemon, weaker durability). Dominated by the shipped channel-postgres for the
  same use case; revisit only if the lighter-daemon tradeoff is specifically wanted.

(The substrate ships `MemoryChannel` + `SqliteChannel` + `PostgresChannel`; a *push*-based
backend is the deferred channel-postgres LISTEN/NOTIFY idea. `SubmititLauncher` /
`RayLauncher` / `K8sLauncher` are the ecosystem adapters below.)

## Derived tools

- [webapp-viewer](webapp-viewer.md) — fancy webapp: lists active runs, tails their
  progress, has a per-run stop button. The topic log is non-destructive, so any
  backend can be tailed by a read-only viewer. FastAPI + WebSockets or SSE.
- ~~cli-status / cli-stop~~ — **SHIPPED 2026-07-16** as `runstate/cli.py`
  (`runstate status <root>` + `runstate stop <root> <run_id> [--wait]`): the
  minimal stdlib-argparse tool over a run's sqlite log, unblocked by the observer
  clock (status reads `last_activity` for freshness). Deliberately not a daemon or
  viewer — that stays webapp-viewer / visualization-story. sqlite only (Postgres
  discovery has no shape — item 3 above).

## Open implementation items (mirrors design §12)

The *deferred* items from `design-v0.2.md §12` — kept there with their full
reasoning, listed here so they're discoverable as work (cross-ref, not moved):

- **Cursor persistence / crash-replay** (§12.5) — the worker's read cursor is
  in-memory; no restart persistence, so the at-least-once / at-most-once boundary
  (a `value` or `count`-`until` over-firing on replay) is unaddressed. *Measured
  2026-07-10:* the refold this would optimize costs ~2 ms at a 10⁶-envelope log;
  the dominant resume term was the attach-time unfiltered read — fixed same day
  via `last_seq()` + the head-first capped attach (design §4/§12.5; 3.4 s → 1.5 ms).
- **Multi-orchestrator attribution** (§12.7–8) — the drain model already makes
  every orchestrator's commands take effect; what's open is *attribution* (whose
  command), today a `request_id`-prefix stopgap.
- **GC / retention policy** (§12.9) — *in-log* retention is full, no GC (the
  precondition `peek_terminal` / resume rely on); *home-level* collection is
  recipe'd (`../specs/store.md` Recipe 3: pointer-rooted mark-and-sweep,
  selective-prune default). In-log compaction remains future work, elaborated
  2026-07-16 into [in-log-compaction](in-log-compaction.md) (DESIGN DELIBERATION,
  NOT CONVERGED, owner-gated: the heartbeat keep-latest candidate, what it does to
  the contiguous-`seq` contract, why the value plane is never compactable, and the
  owner-decision list).

## Deferred from the exogenous-commit audit (2026-06-20)

- **DELETE-mode busy-retry (J3)** — under `journal_mode=DELETE`, `read`/`latest`
  AND the unconditional-append branch of `send` lack the `SQLITE_BUSY` busy-retry
  the CAS path has, so a writer holding the lock past `busy_timeout` surfaces
  `database is locked` rather than waiting. Only reproduced with a *pathological
  unpaced* worker (a realistic paced worker never starved it, and the conformance
  suite runs clean under DELETE); take it up across read **and** unconditional-write
  if a realistic contention case ever reproduces it.
- **`Topic` placement (V3)** — `Topic` lives in `vocabulary/payloads.py` (a body
  module) though it is routing vocabulary; a `vocabulary/topics.py` would be the
  orthogonal home. Marginal.
- **Wontfix-by-design:** the empty-`RUNSTATE_SQLITE_JOURNAL_MODE` `ValueError`
  (J6 — fail-loud is correct), and the `"control.>"` read-glob staying a bare
  literal rather than a `Topic` member (V5 — a glob is not a wire topic).

## Ecosystem adapters (separate packages)

- **[submitit-launcher](submitit-launcher.md)** — `SubmititLauncher` for
  SLURM/AWS Batch/local. The bring-your-own-launcher **recipe ships now**
  (`examples/submitit/`, verified on `cluster="local"`); the design entry
  collects the decisions to promote it to a first-class adapter (in-repo
  `[submitit]` extra vs separate package; callable-vs-cmd target vs the
  [launcher-protocol-typing](launcher-protocol-typing.md) finding; Terminated
  mapping; the `slurm://jobid` handle + `squeue -j` resolve; the requeue ↔
  `steps(start=)` episode synergy).
- **runstate-ray** — `RayLauncher` for Ray actors.
- **runstate-k8s** — `K8sLauncher` for Kubernetes Jobs.
- **runstate-hydra** — Hydra config + sweep adapter; bridges Hydra multirun into a
  runstate sweep manifest.
- **runstate-mlflow** — exporter that mirrors the relational facts (the
  `../specs/store.md` Recipe-5 index sources: pointers, birth records, config
  records) into MLflow's tracking server.
- **runstate-wandb** — convenience for routing Progress events to wandb alongside
  the runstate Channel.

## Tactical

- **Windows support** — untested, unclaimed. The old rationale ("uses
  `fcntl.flock`") died with the v0.1 file backend — v0.2 holds no file locks
  (sqlite serializes internally; memory uses a threading lock). The actual
  suspects now: the `os.kill(pid, 0)` handle-probe semantics, the signal-based
  `Terminated(killed, signal=-rc)` mapping, and the fork-only concurrency tests.
- **xxhash** — faster file hashing for the reference `run_id()` recipe; stdlib
  SHA-256 is fine until codebase sizes get unwieldy.
- **Schema codegen for other languages** — Rust types via `quicktype` /
  `datamodel-code-generator` analogs. Test that generated types round-trip through
  the schema.
- **Protocol versioning** — v0.2 versions each convention schema on its own timeline
  (version-suffixed `$id`). Still open: v0.1↔v0.2 coexistence semantics on one
  channel, and how a reader negotiates the convention version.
- **`sweep` under placement** — `sweep`'s one-root assumption needs a per-rid
  wrapper under content-addressed placement (`../specs/run-id-recipe.md`).
- **[memoizer-index-algebra](memoizer-index-algebra.md)** — a derived, rebuildable,
  never-authoritative membership index over the value log; **dormant** until a
  consumer needs cross-run enumeration faster than a scan (the named trigger;
  `../specs/store.md` Recipe 5).

## Documentation

- **README recipes** — remaining: the synchronous-yield RPC pattern, and
  multi-orchestrator (a launcher + a separate UI sending stops). The
  reuse/divergence-preempt/killed-redrive recipes shipped.
- ~~**Protocol-implementer's guide**~~ — **SHIPPED 2026-07-16** as
  [`../implementers-guide.md`](../implementers-guide.md): the language-neutral
  reference for a non-Python implementation (Rust, Go, TS) — two-tier conformance,
  the substrate contract, the conventions at their current wire versions, and the
  audit's harvest of what a non-Python implementer cannot infer without reading the
  Python (the public raise-contract table; the `in_process`/`cross_process`/
  `cross_host` tier ladder; the `RunResult.reason` per-tier vocabulary; the Postgres
  interop constants; the full topic-pattern grammar; the half-open window fencepost
  `until={"step": N}` = `[0, N)`, reached iff `progress + 1 >= N`). Its wire
  examples are drift-guarded by `tests/test_implementers_guide.py` (the
  `test_schema.py` mechanic). Three tree-vs-note corrections folded in: only the
  *verdict* folds raise `MalformedRecordError` (measurement folds skip); `history`
  has no divergence raise since G1 (take-the-latest); `open_channel`'s `ValueError`
  covers both `root=None` cases, not only a bad backend.
- [protocol-algebra](protocol-algebra.md) — the principled constructions behind the
  layer interfaces (L1 free-monoid initiality, L2 designated intro/elim discipline +
  discharge folds = the context Γ, L3 observer-join), each yielding a **decision
  rule**, with retrodictions and the rejected-formalisms negative space. **Placement
  partly resolved (2026-07-16):** the reader-facing seed (the three decision rules +
  the intro/elim table) now lives in the implementer's guide "why layer" (§7,
  dated); the **formal** treatment's final home (design appendix vs `overview.md`
  incorporation) is still open.
