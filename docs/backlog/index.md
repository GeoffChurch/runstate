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
discoverable work list; the design's deferred §12 items are mirrored below under
*Open implementation items*). The big-ticket threads:

- **Run episodes** ([run-episodes](run-episodes.md)) — unifies lazy-launch, the
  lifeline service-worker, and the completed-but-extendable gap.
- **The relational layer** — the **Store** (the real component), plus a
  `run_id()` *recipe* (the re-scoped "Hasher") and the dedup-vs-enumeration
  split (below), driven by [mycooc-adoption](mycooc-adoption.md), the
  validating use case.
- **Visualization** ([visualization-story](visualization-story.md)) — the
  long-horizon data-plane protocol; post-relational-layer.
- **v0.3 data-plane exploration** ([../design-v0.3-exploration.md](../design-v0.3-exploration.md))
  — metric series as the log/cache, the history/memoizer helper, `value.step`
  present-nullable, and the launcher-vs-worker = *viewpoint* realization.
  Forward thinking; the v0.2 wire is unchanged.

## Long-term ambition

- [visualization-story](visualization-story.md) — own the data-plane
  visualization protocols too (richer event types: Histogram, Image,
  Tensor; viewer-discovery protocol; artifact-storage protocol). Become
  a one-stop shop instead of running alongside wandb / MLflow / TB.
  Strictly post-v0.2; depends on Store landing first.

## Protocol extensions (control plane)

- [run-episodes](run-episodes.md) — a `run_id` is a durable log hosting
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

## Layer 4 — the relational layer: the Store (and why the Hasher is a recipe)

Re-scoped 2026-05 (the reasoning is worth keeping). The headline component is
the **Store**; the once-planned "Hasher Protocol + DefaultHasher" collapses to a
*recipe*.

- **Store Protocol + backends** — *the real Layer-4 work.* Relational metadata
  for runs and experiments; many-to-many `Run × Experiment` membership;
  cross-run enumeration ("all seeds of this variant", "all runs at commit Y").
  Backends: FileStore (zero deps), SqliteStore (central index), PostgresStore.
  This is the structure a content-addressed `run_id` *discards*, so nothing
  else can supply it.

- **Why the Hasher is a recipe, not a component.** A hasher is just a function
  `h: Inputs → Keys` whose kernel must *refine* the run's input→output kernel
  (`h(i₁)=h(i₂) ⟹ run(i₁)=run(i₂)` is exactly the condition for reuse to be
  correct). Its entire content is the *choice of partition* — which inputs
  count, how they're canonicalized — which is workload-specific, so it belongs
  in user code. The protocol recognizes no fingerprint, so a `Hasher`
  *Protocol* earns no place unless a consumer (the Store, a reuse helper) takes
  one polymorphically — i.e. bundled with that consumer, never a standalone
  slice.

- **Reuse-by-hash splits in two:**
  - *Dedup is already free in the substrate.* `run_id` is opaque/caller-chosen,
    so set `run_id = h(inputs)`; then "has this run happened?" =
    `open_channel(run_id)` exists ∧ `peek_terminal(...)` terminal. No new API.
    (Composes with [run-episodes](run-episodes.md): same inputs → same run_id →
    same log → idempotent relaunch / resume.)
  - *Cross-run reuse + enumeration need the Store* — a relational query the
    per-run topic log structurally can't answer.

- **The reference `run_id()` recipe** — capture git state **by content** (hash
  the blob bytes that actually run), *not* by inferring clean-vs-dirty. mycooc's
  `_compute_git_fingerprint` + `_fingerprints_compatible` over-distinguish (a
  dirty-but-byte-identical file reads as changed → false cache *miss*) and then
  hand-code a re-identification predicate to repair it; hashing content makes
  the kernel track what matters and the predicate evaporates. The failure mode
  to warn about is the opposite: *omitting* an output-determining input (data,
  lib/CUDA version) → false *hit* = silently-wrong reuse.

See [mycooc-adoption](mycooc-adoption.md) — the validating use case; its
`_compute_config_hash` / `_find_reusable_run` are the reference for the
`run_id()` recipe and the Store's reuse query.

(Shipped in v0.2: the sequential `sweep` helper + `Variant`. A *Cartesian*
config sweep on top of it, and Store-backed reuse skipping, remain.)

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
- **GC / retention policy** (§12.9) — retention is full, no GC (the precondition
  `peek_terminal` / resume rely on); a policy is future work.

## Ecosystem adapters (separate packages)

- **runstate-submitit** — `SubmititLauncher` for SLURM/AWS Batch/local.
- **runstate-ray** — `RayLauncher` for Ray actors.
- **runstate-k8s** — `K8sLauncher` for Kubernetes Jobs.
- **runstate-hydra** — Hydra config + sweep adapter; bridges Hydra
  multirun into a runstate sweep manifest.
- **runstate-mlflow** — exporter that mirrors runstate Store entries
  into MLflow's tracking server (when Store ships).
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
