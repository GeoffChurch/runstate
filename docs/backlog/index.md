# Backlog

Forward-looking ideas for runstate. One file per idea where it warrants
elaboration; smaller items can live inline here under each section.

Convention: when an idea is executed, delete its file or remove its
inline entry. Refuted ideas (with diagnosis) move to `docs/dead_ends/`
parallel to this directory.

## Long-term ambition

- [visualization-story](visualization-story.md) — own the data-plane
  visualization protocols too (richer event types: Histogram, Image,
  Tensor; viewer-discovery protocol; artifact-storage protocol). Become
  a one-stop shop instead of running alongside wandb / MLflow / TB.
  Strictly post-v0.2; depends on Store landing first.

## Protocol extensions (control plane)

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
- **Launcher Protocol** — currently only Channel has a backend protocol.
  Add Launcher + LocalLauncher / ThreadLauncher / SubmititLauncher etc.
  for users who want library-managed process spawning. v0.2 once we have
  the Store and want a more featureful orchestration layer.
- **FileChannel: inotify polling** — sub-50ms recv latency on Linux.
  Currently we poll every 50ms. Worth doing if tight inner loops appear.

## Derived tools

- [webapp-viewer](webapp-viewer.md) — fancy webapp: lists active runs,
  tails their progress, has a per-run stop button. SqliteChannel-only
  (FileChannel deletes consumed messages and can't be tailed by a
  read-only viewer). FastAPI + WebSockets or SSE. ~300-500 LOC.
- [cli-status](cli-status.md) — terminal status table (like
  `mycooc/run_experiment.py --status`) reading directly from
  SqliteChannel `messages` table. Maybe `runstate status <root>`.
- [cli-stop](cli-stop.md) — one-shot CLI: `runstate stop <run_id>`
  opens an orchestrator-role Channel and sends StopNow. ~30 LOC.

## v0.2 — relational layer

- **Store Protocol + backends** — relational metadata for runs and
  experiments. Many-to-many `Run × Experiment` membership. Backends:
  FileStore (zero deps), SqliteStore (central index), PostgresStore.
- **Hasher Protocol + DefaultHasher** — content-addressable input
  fingerprinting (config + git + code files + seed).
- **Reuse-by-hash** — orchestrator (or any helper) consults Store
  before launching; if matching run exists and is DONE, reuse instead.
- **Sweep helper** — produces a Cartesian sweep of configs and
  dispatches via the orchestrator pattern of the user's choice.

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
- **xxhash** — faster file hashing for the future Hasher; stdlib
  SHA-256 is fine until codebase sizes get unwieldy.
- **Schema codegen for other languages** — Rust types via
  `quicktype` / `datamodel-code-generator` analogs. Test that
  generated types round-trip through the schema.
- **Protocol versioning** — currently v0.1 hardcoded in `$id`. When
  v0.2 ships, decide on coexistence semantics (separate Channels per
  version vs version field in envelope).
- **Test that schema and dataclasses stay in sync** — a single test
  that auto-generates instances of every Command/Event dataclass and
  validates each against the schema. Catches drift between the two
  source-of-truth artifacts.

## Documentation

- **Recipes section in README** — at least three recipes: divergence
  preempt (the canonical case), synchronous-yield RPC pattern,
  multi-orchestrator (a launcher + a separate UI sending stops).
- **Protocol-implementer's guide** — a doc for someone writing a
  non-Python implementation (Rust, Go, TS). What conformance means;
  what tests to write; how to interop with the Python reference.
