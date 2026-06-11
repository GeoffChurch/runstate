# Backlog

Forward-looking ideas for runstate. One file per idea where it warrants
elaboration; smaller items can live inline here under each section.

Convention: when an idea is executed, delete its file or remove its
inline entry. Refuted ideas (with diagnosis) move to `docs/dead_ends/`
parallel to this directory.

## Start here (taking on the upcoming work)

A fresh session should read, in order: **`../../CLAUDE.md`** (orientation + the
shipped-vs-deferred scope snapshot) → **`../design-v0.2.md`** (the converged
design; §12 = open implementation items, §14 = scope) → **this file** (the
discoverable work list). For the v0.3 thread, also read the shipped specs
**`../specs/run-episodes.md`**, **`../specs/run-id-recipe.md`**,
**`../specs/memoizer.md`**, and **`../specs/store.md`** (the arc's largest
dissolution — the relational layer; decision trail
[store-deliberation](store-deliberation.md)), and the trail
**`../design-v0.3-exploration.md`**.
For the **dependency/synergy view** over this list — the items grouped into
clusters that unlock each other, with a sequencing — see
[synergy-map](synergy-map.md).

**Most recent — 2026-06-02: the memoizer shipped** (on `master`): `history()`
replays the subscription algebra over the logged `value` points (passive,
channel-only, worker-invisible) and `ensure(producer, name, up_to=N)` serves the
logged prefix on a hit or relaunches-to-extend and waits on a miss (progress from
the dense heartbeat axis, content from the value series; race-free handle-based
wait), plus `launch_producer` (the producer seam) and `relaunch_if_needed` (the
live-check + launch helper). Also: `value.t` → absolute wall-clock (the reader
projects run-relative). (Spec: `../specs/memoizer.md`; plan:
`../plans/2026-06-02-memoizer.md`.) Two adversarial *orthonormal-basis* design
reviews ran alongside — the rubric is now in `../../CLAUDE.md` ("Design rigor")
and the residual convention findings are in
[conventions-hygiene](conventions-hygiene.md).

**Prior — 2026-06-01: the run-episodes scoped primitive shipped:** CAS
`send(expected_seq=)`, episode-aware `peek_terminal` / `live_episode`,
`Worker.steps(start=)`, `handle.resolve()`, the **worker self-claims its episode**
guard — plus an autonomous-extend integration test and the
`run_id`-excludes-target recipe rule. (Spec: `../specs/run-episodes.md`; plan:
`../plans/2026-06-01-run-episodes.md`.)

**→ Next pickups:**

- **[mycooc-migration-audit](mycooc-migration-audit.md)** — findings from the *completed* mycooc migration (first end-to-end consumer) + a follow-up audit. **✅ The VERIFIED P0 CAS-atomicity bug (F1) is fixed** (2026-06-07; fix superseded 2026-06-09 by the atomic-by-construction form — mechanism + corrected diagnosis live in the file's F1). **✅ The lost/clobbered `control.stop` (F2/F3) is fixed** (2026-06-09 — [stop-discharge](../specs/stop-discharge.md)). **✅ The reader gaps (F5–F8) are shipped** (2026-06-10 — [observables](../specs/observables.md): `value_series`/`progress`/`latest_episode`/`handle_pid`; mycooc deletes its hand-rolled files in one sweep). Remaining in the file: F4 (channel lifecycle/`close`) + the F9/F10 minors.
- **[ensure-redrive-recoverable-terminations](ensure-redrive-recoverable-terminations.md)** — let `ensure` re-drive killed/timed-out runs that made progress, instead of raising; subsumes the consumer's custom resume loop. Surfaced by mycooc Phase-4 dogfood (the `_SyncHandle` terminal-synthesis and `_run_one_chunk` resume loop are the dual of this missing feature). Not bit-exact-testable; needs a mock-producer approach.
- **Cluster 1, remaining halves** — the **service worker SHIPPED 2026-06-10**
  (`../specs/service-worker.md`) and **episode-scoped time-leases SHIPPED
  2026-06-11** (`../specs/time-lease-boundary.md` — the boundary `started`
  is the lease's counter-record; ghost relaunches bounded ≤2 by
  construction). **Lazy-launch SHIPPED
  2026-06-11** (`../specs/lazy-launch.md`: `ensure_served` + the
  foreign-claim-scoped reap discipline + hostname-scoped `resolve` + the
  loser guard on `stopped()`; the activator daemon stays a recipe until a
  launch-ignorant demander exists — the named promotion trigger). **The function
  producer RESOLVED BY DISSOLUTION 2026-06-11** (`../specs/derived-runs.md`:
  one derived run per analyzed snapshot — full-read-set content identity,
  the one-step-run convention, the existing `ensure`; no new library
  surface, no named Protocol (three implementers, one unchanged seam), the
  [index algebra](memoizer-index-algebra.md) dormant-with-trigger).
  **Cluster 1 is CLOSED**, and the mycooc-side wiring landed the same day
  (`ensure_analysis` + the one-step `--worker`, cached-by-default CLI;
  record in the spec).
- **The relational layer — DISSOLVED 2026-06-11** (`../specs/store.md`;
  decision trail [store-deliberation](store-deliberation.md)): the Store
  ships as recipes over the existing basis (rid-as-address, cell pointers,
  the child's birth record, a derived never-authoritative index) plus one
  helper (`foreign_episode`, the producer gate's foreign half). The
  `run_id()` *recipe* had already shipped (`../specs/run-id-recipe.md`);
  [mycooc-adoption](mycooc-adoption.md) remains the validating-consumer
  ledger. What remains is the **mycooc wiring plan** (the cell/run split
  migration — the arc's largest consumer migration; separate artifact).
- **[conventions-hygiene](conventions-hygiene.md)** — *mostly resolved 2026-06-02
  (Thread A):* cut phantom `lifecycle.phase` (F1), dropped dead `RunResult.elapsed`
  (F8), gave `consumed_seq` the `await_consumed` consumer (F3). Only the pid
  `?start=` disambiguator (F9) remains — deferred (rationale in the file).
- **Visualization** ([visualization-story](visualization-story.md)) — the
  long-horizon data-plane protocol. Its old "Store lands first" dependency
  re-keys to the root set + the Recipe-5 index (`../specs/store.md`);
  viewer discovery = list the roots, follow pointers and birth records.

**Live deferred items off run-episodes:** the cross-episode control-cursor item
is **shipped** ([stop-discharge](../specs/stop-discharge.md), specced and
implemented 2026-06-09; the strict-xfail pin now passes). (The service/lifeline
policy is a Next pickup above; the best-effort launch pre-check / idempotent
relaunch shipped with the memoizer as the free `relaunch_if_needed` helper — a
log-read + `launch`, deliberately **not** a `Launcher` Protocol method.
Consumer-side cursor persistence was **decided out of scope**, design §12.5.)

## Long-term ambition

- [visualization-story](visualization-story.md) — own the data-plane
  visualization protocols too (richer event types: Histogram, Image,
  Tensor; viewer-discovery protocol; artifact-storage protocol). Become
  a one-stop shop instead of running alongside wandb / MLflow / TB.
  Strictly post-v0.2; discovery rides the dissolved relational layer
  (`../specs/store.md`: the root set + pointers + the dormant index).

## Protocol extensions (control plane)

- [run-episodes](run-episodes.md) — **scoped primitive + autonomous-extend SHIPPED
  2026-06-01** (`../specs/run-episodes.md`); the idempotent-relaunch / launch
  pre-check shipped 2026-06-02 as the memoizer's `relaunch_if_needed` helper;
  *remaining:* the service/lifeline policy (a Next pickup above). — a `run_id` is a durable log hosting
  *multiple worker episodes*; relaunch reuses the `run_id` and the worker
  resumes from run-keyed state. Unifies lazy-launch (§12.1), the lifeline
  service-worker, and the "completed-but-extendable" gap (mycooc), with a
  service (lifeline-driven) vs autonomous (target-driven) policy split.
  Requires episode-aware `peek_terminal`/liveness.
- **`lifecycle.stopped.reason` vocabulary recipe** — an opt-in documented
  vocabulary (e.g. `completed`/`preempted`/`converged`/`budget_exhausted`,
  resumable-`timed_out` vs fatal-`crashed`) so consumers can branch on *why*
  without expanding the closed `outcome` enum. Surfaced by
  [mycooc-adoption](mycooc-adoption.md). Small.
- **Cross-host liveness for the claim gate** — `live_episode` treats an
  unresolvable handle as live *forever* (no staleness fallback): a crashed
  episode under a foreign/renamed hostname blocks both the waker AND the
  worker birth-CAS until a manual `stopped`. Fine single-host (the shipped
  scope, `../specs/lazy-launch.md`); a staleness-based liveness tier is
  needed before cross-host / shared-FS resume. Surfaced by the lazy-launch
  review.
- **Watcher boundary-aware re-broadcast** — a time-keyed `broadcast` barrier
  subscription on a run that *resumes* is boundary-voided with no record
  (`../specs/time-lease-boundary.md`); today's steering is "spell barriers
  step-keyed" (design §9). A Watcher that watches `started`s and re-issues
  its broadcast across boundaries would lift the restriction. Small.
- **Time-axis unification** — three time anchorings now coexist by design:
  `history()` replays time atoms run-epoch-anchored; live subscriptions are
  episode-scoped (`time-lease-boundary`); stops re-anchor per episode
  (stop-discharge's note). Each is locally right; if a consumer ever needs
  them to agree, unify deliberately rather than ad hoc.
- **Discharge-by-id (merge-tolerant control folds)** — now TWO positional
  rules to generalize (design §7's pairing-by-`seq`: the stop discharge AND
  the subscribe answer fold — sharpened by the worker itself now writing
  `control.unsubscribe` expiry records) — generalize the
  stop-discharge fold's positional rule ("pending until the *next*
  `lifecycle.stopped`", `../specs/stop-discharge.md`) to explicit causal
  reference: a `stopped` names the `request_id`(s) it discharges. Makes the
  control fold commutative, so it survives multi-writer `control.*` (§12.7–8)
  and replicated logs, where "next" is not well-defined — only the run-local
  log has a total order; spacelike-separated writers have only the causal
  partial order. Surfaced 2026-06-09 by stress-testing the global-seq design
  against a galaxy-scale topology. No-op while every log has a single home;
  revisit with the Postgres/Redis backends or any replication story.
- [protocol-async-api](protocol-async-api.md) — wrap the JSON Schema in
  AsyncAPI for a richer spec format (multi-channel, lifecycle events).
  Defer until v0.2 protocol grows enough to justify the layer.
- **Reconfigure command** — typed orchestrator-to-worker command for
  mid-flight hyperparameter changes. Dropped from v0.1 as speculative;
  add when at least 3 concrete validated use cases exist.
- **Pause / Resume commands** — for cluster-preemption or "another job
  needs the GPU" scenarios. v0.2 if a real use case emerges; for now
  Stop + restart externally is the recommended pattern.
- **Snapshot command** — orchestrator asks worker to checkpoint without
  exiting. Workload-specific; users can define their own dict shape via
  raw Channel. Promote to first-class if a common shape emerges.
- **Cleanup / resource-pressure commands** — `Cleanup(level: str)` for
  "GPU memory pressure, please free buffers". Too workload-specific to
  standardize; leave to user-defined dicts.

## Backends

- [channel-postgres](backends/channel-postgres.md) — Postgres backend
  for Channel using LISTEN/NOTIFY for push semantics. Natural fit for
  multi-host orchestration where shared FS is fragile or absent.
- [channel-redis](backends/channel-redis.md) — Redis backend; alternative
  to Postgres for cross-host scenarios. Lighter daemon but weaker
  durability story.

(Shipped in v0.2: the `Launcher` Protocol + `ThreadLauncher` / `LocalLauncher`.
`SubmititLauncher` / `RayLauncher` / `K8sLauncher` remain — see ecosystem
adapters below. The substrate has only `MemoryChannel` + `SqliteChannel`; a
push-based backend is the channel-postgres LISTEN/NOTIFY idea above.)

## Derived tools

- [webapp-viewer](webapp-viewer.md) — fancy webapp: lists active runs,
  tails their progress, has a per-run stop button. The topic log is
  non-destructive, so any backend can be tailed by a read-only viewer.
  FastAPI + WebSockets or SSE. ~300-500 LOC.
- [cli-status](cli-status.md) — terminal status table reading directly
  from the SqliteChannel `log` table. Maybe `runstate status <root>`.
- [cli-stop](cli-stop.md) — one-shot CLI: `runstate stop <run_id>`
  opens the run's channel and sends a `control.stop`. ~30 LOC.

## Layer 4 — the relational layer: DISSOLVED (2026-06-11)

The Store is settled: **`../specs/store.md`** (decision trail:
[store-deliberation](store-deliberation.md); the May framing that lived
here — "Store Protocol + backends: FileStore/SqliteStore/PostgresStore;
the structure a content-addressed `run_id` discards, so nothing else can
supply it" — is retired by the per-fact dissolution). The shape that
shipped:

- **rid → location is the channel's address** (content-addressed
  placement, `runs/<rid[:2]>/<rid>/`; cells are thin pointer dirs) — and
  reuse-by-hash dissolves into `ensure` against the one home, arbitrated
  by the shipped birth-CAS. The May split ("dedup is free; cross-run
  reuse + enumeration need the Store") collapses: placement makes the
  reuse query *also* free, and enumeration = the root set, a configured
  constant.
- **Membership** = the cell pointer (current) + the consumer's tracked
  tabulated overview (archival); **provenance** = a backward record on
  the derived run's own log; **any index** = a derived, rebuildable,
  never-authoritative consumer-side cache (dormant-with-trigger).
- One library helper by the F7 doctrine: `foreign_episode` (the producer
  gate's foreign half; the `extend` seam contract revised — see
  `../specs/memoizer.md` Decision 5).

Still true and still here: **the Hasher is a recipe, not a component** —
a hasher's kernel must refine the run's input→output kernel
(`h(i₁)=h(i₂) ⟹ run(i₁)=run(i₂)`), its whole content is the
workload-specific choice of partition, and the reference recipe is
`../specs/run-id-recipe.md` (git state by content; the false-hit warning
— sharpened under placement, where a false hit silently *converges* two
intended computations). [mycooc-adoption](mycooc-adoption.md) remains the
validating-consumer ledger.

(Shipped in v0.2: the sequential `sweep` helper + `Variant`. A *Cartesian*
config sweep on top of it remains app-side; reuse-skipping dissolved into
`ensure`-against-the-home. Note `sweep`'s one-root assumption needs a
per-rid wrapper under placement — `../specs/run-id-recipe.md`.)

## Open implementation items (mirrors design §12)

The *deferred* items from `design-v0.2.md §12` — kept there with their full
reasoning, listed here so they're discoverable as work (cross-ref, not moved):

- **Lazy-launch double-spawn guard** (§12.1) — elaborated in
  [run-episodes](run-episodes.md) (the guard *is* idempotent relaunch).
- **Cursor persistence / crash-replay** (§12.5) — the worker's read cursor is
  in-memory; no restart persistence, so the at-least-once / at-most-once boundary
  (a `value` or `count`-`until` over-firing on replay) is unaddressed.
- **Multi-orchestrator attribution** (§12.7–8) — the drain model already makes
  every orchestrator's commands take effect; what's open is *attribution* (whose
  command), today a `request_id`-prefix stopgap.
- **GC / retention policy** (§12.9) — *in-log* retention is full, no GC (the
  precondition `peek_terminal` / resume rely on); *home-level* collection is
  recipe'd (`../specs/store.md` Recipe 3: pointer-rooted mark-and-sweep,
  selective-prune default). In-log compaction remains future work.

## Conventions hygiene (2026-06 basis audit)

- [conventions-hygiene](conventions-hygiene.md) — findings from the adversarial
  orthonormal-basis audit of the L2 conventions. **F1/F8/F3 resolved 2026-06-02
  (Thread A):** phantom `lifecycle.phase` cut, dead `RunResult.elapsed` dropped,
  `consumed_seq` given the `await_consumed` consumer. **F9 deferred:** the pid
  `?start=` disambiguator (rationale in the file). The basis itself audited as
  largely tight.

## Ecosystem adapters (separate packages)

- **runstate-submitit** — `SubmititLauncher` for SLURM/AWS Batch/local.
- **runstate-ray** — `RayLauncher` for Ray actors.
- **runstate-k8s** — `K8sLauncher` for Kubernetes Jobs.
- **runstate-hydra** — Hydra config + sweep adapter; bridges Hydra
  multirun into a runstate sweep manifest.
- **runstate-mlflow** — exporter that mirrors the relational facts (the
  `../specs/store.md` Recipe-5 index sources: pointers, birth records,
  config records) into MLflow's tracking server.
- **runstate-wandb** — convenience for routing Progress events to wandb
  alongside the runstate Channel.

## Tactical

- **Windows support** — currently Unix-only (uses `fcntl.flock`).
  Add `portalocker` as an optional dep for cross-platform file locking.
- **xxhash** — faster file hashing for the reference `run_id()` recipe;
  stdlib SHA-256 is fine until codebase sizes get unwieldy.
- **Schema codegen for other languages** — Rust types via
  `quicktype` / `datamodel-code-generator` analogs. Test that
  generated types round-trip through the schema.
- **Protocol versioning** — v0.2 versions each convention schema on its
  own timeline (version-suffixed `$id`). Still open: v0.1↔v0.2
  coexistence semantics on one channel, and how a reader negotiates the
  convention version. (Schema/impl drift is now guarded: `test_schema.py`
  validates the messages the implementation actually emits against the
  schema stack.)

## Documentation

- **Recipes section in README** — at least three recipes: divergence
  preempt (the canonical case), synchronous-yield RPC pattern,
  multi-orchestrator (a launcher + a separate UI sending stops).
- **Protocol-implementer's guide** — a doc for someone writing a
  non-Python implementation (Rust, Go, TS). What conformance means;
  what tests to write; how to interop with the Python reference.
- [protocol-algebra](protocol-algebra.md) — the principled constructions
  behind the layer interfaces (L1 free-monoid initiality, L2 designated
  intro/elim discipline + discharge folds = the context Γ, L3
  observer-join), each yielding a **decision rule**, with retrodictions
  (F2 as a type error; the refuted A2 as a category error) and the
  rejected-formalisms negative space. **Placement open** — design appendix
  vs `overview.md` incorporation; seeds the implementer's guide above.
