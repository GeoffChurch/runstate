# Changelog

All notable changes to runstate are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
runstate has **not yet cut a public release**. The first will be **0.4.0**, which writes log
format 0.4.0; what a version promises is ruled in
[`docs/specs/release-and-stability.md`](docs/specs/release-and-stability.md), and when it is tagged
is in [`docs/backlog/index.md`](docs/backlog/index.md) ("The 0.4.0 release").

Reconstructed from the repo's own records — `docs/design-v0.2.md` (revision
history), the dated status lines in `docs/specs/*`, and `git log master`. Not a
substitute for those; a reading order over them.

## [Unreleased]

The post-v0.2 arc, landed on `master`, not yet released; it becomes 0.4.0. Grouped by
the thread that produced each change, newest first.

### Release and stability (2026-11-02)

- **The release contract is ruled** (`docs/specs/release-and-stability.md`). The log
  format is the wire's one promised version. Before 1.0, a MINOR release may change it
  (always with a retained `runstate migrate` step) or break `runstate.__all__`, and a
  PATCH does neither; from 1.0, either is a MAJOR. There are no deprecation windows.
  The wheel carries no schemas; the sdist and the repository do.
- **Every registered format's schemas are kept, in `protocol/v<format>/`**, named as a
  log's address names its format: `protocol/v0.2.0/`, `v0.3.0/` and `v0.4.0/`. The
  0.2.0 and 0.3.0 stacks move there from the migration tests' fixtures.
- **The bodies and their schemas are compared over generated bodies.**
  `tests/test_payloads.py` checks that a body parses if and only if its schema
  definition admits it, now over ~17k bodies generated from each definition as well as
  the hand-picked edges.

### Log format 0.4.0 — commits and lineage (breaking; 2026-10-31)

`docs/specs/commits-and-lineage.md`, executed by `docs/plans/2026-10-28-layer2-live.md`.
`runstate.LOG_FORMAT` is 0.4.0 and the package is `0.4.0.dev0`. `runstate migrate`
moves a 0.2.0 or a 0.3.0 log to it through the retained 0.3.0 → 0.4.0 step, which
runs a detection pass first and takes a consumer's facts and choices
(`--assume`, `--resolve`, `--accept`).

- **Values are committed to a lineage of nodes.** Each `commit()` writes a
  `lifecycle.commit` node naming its parent and the values it commits. A worker's
  exit is a node too, and `lifecycle.rewind` restates the position. Every lineage
  read (`series`, `prefix`, `prefix_len`, `last_element`, `aligned`, `answers`,
  `streams`) takes a required `head` (`LatestClaimHead()`, `AtNode(node)`), so a
  rewind, a resume from an older checkpoint or a displaced worker branches rather than
  splicing.
- **Progress is a count along the lineage:** `Commits()`, or an eager stream's
  length, and `Target(x, n)`, "the first n of x". The subscription algebra counts the
  same way (`{commits: n}`, `{stream: name, n: n}`), with no `step`. `ensure`
  returns `Reached | Unreachable`.
- **The Worker** (`Worker(channel, *, resume_from, sources, save)`) claims from a node:
  - `emit`, `set` and `include` write values;
  - `commit()` ends an iteration;
  - `rewind`, `discard`, `stopped` and `retire` change its position or end it.
- **`control.withdraw`** cancels a stop or a subscription by its id, replacing
  `control.unsubscribe`.
- **`launcher.*` is renamed `supervisor.*`**, and **`supervisor.died`** is a death
  report naming the claim it ends. A launcher's reap writes it, as do
  `report_if_dead` and any party with evidence through `report_death`. A claim
  ends by its exit or a death report, by presence (`ended`), never by a probe's
  silence.
- **The verdict is by viewpoint:** `RunResult.outcomes` (a set of the closed `Outcome`
  enum), `tier` and `reports`.
- **Held folds** for a polled path, each equal to its pure read after every record:
  `HeadFold`, `PrefixLen`, `LastElement`, `Answers`, `LiveDemand`, `PendingStops`.
- **The checkpoint recipe** (`runstate/checkpoint.py`): the `SavePolicy` Strategy
  (`Every(k, write)`, `NoSave()`) saves in two phases: the bytes before the node, and
  the name after it (#62). The `CheckpointChoice` Strategy (`OnHeadLineage()`,
  `MostProgress(stream)`) chooses among checkpoints.
- **`Launcher[T]`**, typed by its target record (`ThreadTarget`, `Command`).
- **`sweep` and `Variant` are deleted** (2026-10-30), a workload helper no consumer
  called (`docs/dead_ends/sweep.md`).
- **The SQLite name rules and the address scheme's inverse** (2026-11-02, #64). A run
  id is one filename, and a root is not named `vX.Y.Z`; together they make the address
  scheme one-to-one, and `formats.sqlite_address(path)` inverts it.

### The session probe on Postgres (2026-10-30)

- Every `PostgresChannel` connection takes a fresh advisory-lock key before its first
  write, and every row carries its writer's stamp. `SessionProbe` reads whether the
  session that wrote a claim has ended, as evidence for a death report, never as a
  claim arbiter; the `Watcher` reads it as the `session_ended` tier. It replaces the
  episode lock (`EpisodeHolder` / `EpisodeProbe`). PostgreSQL 16 or newer.

### Log formats (breaking)

- **A log's format is part of its address** (2026-10-03,
  `docs/specs/log-formats.md`). Logs live at `<root>/v<format>/<rid>.db` (a schema
  per format on Postgres). The locators raise `LogFormatMismatch` on a newer or an
  older format and `LogFormatMissing` on a legacy unversioned log, and
  `runstate migrate` moves logs forward through retained, chained steps that copy
  and seal. Format 0.2.0 names the logs written since `4729fcd`, and format 0.3.0
  is reference by name (below). Format 0.4.0 (above) is current.

### Library API (breaking)

- **`await_consumed(channel, seq)` takes the request's id from its record**
  (2026-10-03). The `request_id` parameter is gone: it was a second source of
  truth, and a missing or mismatched one returned `None` ("accepted") while the
  worker refused the request. A subscribe, unsubscribe or stop at `seq` with no
  `request_id` now raises `ValueError`, and a stop its terminal `stopped` honored
  returns `None` rather than the refused-by-death verdict.

### Wire protocol versions (breaking)

Each convention schema in `protocol/` is versioned on its own timeline and is
`additionalProperties: false`, so any field change is a deliberate version bump
(never a silent addition). The envelope (`envelope-v0.2`) and the value
convention (`value-v0.2`) are unchanged since v0.2. The subscription convention
bumped once, the launcher convention twice, and the lifecycle convention three
times:

- **lifecycle `v0.2 → v0.3`** (2026-07-10) — dropped the dead `Started.hostname`
  field (the first-ever convention bump). Its migration script converged and was
  then deleted per the develop-by-migration doctrine; git carries it.
- **launcher `v0.2 → v0.3`** (2026-07-14) — launch identity: `launcher.launched`
  and `launcher.terminated` carry a per-launch correlation id, re-emitted on the
  worker's `lifecycle.started`, so a late reap or a claim-race loser's clean exit
  can no longer forge the run's verdict (`docs/specs/launcher-record-identity.md`).
  Its migration script converged and was then deleted; git carries it.
- **lifecycle `v0.3 → v0.4` and launcher `v0.3 → v0.4`** (2026-07-16) — the
  observer clock: a required, non-null `t` (the emitter's wall-clock at emission)
  on `heartbeat` / `stopped` / `launched` / `terminated`, and `Started.attached_at`
  renamed to `t` (`docs/specs/observer-clock.md`). Existing logs migrated
  offline via a quiescence-gated script that converged and was then deleted per
  the develop-by-migration doctrine, exactly as the two bumps above; git
  carries it. (That doctrine ended once consumers pinned runstate: migration
  steps are now retained, `docs/specs/log-formats.md`.)
- **lifecycle `v0.4 → v0.5` and subscription `v0.2 → v0.3`** (2026-10-03) —
  reference by name: `heartbeat` and `stopped` carry `claim_seq`, the episode
  they speak for; `stopped.honored` lists the stops it answers; the new
  `lifecycle.bound` binds a lease to the episode that registered it; and
  `control.stop` requires a `request_id` (`docs/specs/reference-by-name.md`).
  The control-plane folds no longer depend on arrival order, which fixes #39,
  the claim cascade and the stale-beat leak. Its migration is retained, as log
  format 0.3.0, reached by `runstate migrate`.
- **lifecycle `v0.5 → v0.6`, subscription `v0.3 → v0.4`, value `v0.2 → v0.3`, and
  launcher `v0.4` → supervisor `v0.5`** (2026-10-31): commits and lineage, log format
  0.4.0 (above). `lifecycle.commit` and `lifecycle.rewind` are new, and `stopped` is a
  node; the schedules count commits and streams; `control.withdraw` replaces
  `control.unsubscribe`; a value's body drops `step`; the `launcher.*` topics become
  `supervisor.*`, with `supervisor.died` new. Its migration is retained, as log format
  0.4.0.

### June 2026 — the v0.3 thread (run-episodes → the store dissolution)

- **Run-episodes + the substrate CAS** (2026-06-01, `docs/specs/run-episodes.md`).
  A `run_id` names a durable log that hosts multiple resumable worker *episodes*
  (`started … stopped`, then a later `started …` on the same log) — relaunch-to-
  extend, launch-on-demand, and reconnect all fall out of it. `send(expected_seq=)`
  added as the substrate compare-and-append; the single-spawn guard is a worker
  self-claim (the CAS'd `lifecycle.started`, the loser exits before acting).
  `steps(start=)` for run-absolute resume; episode-aware `peek_terminal`.
- **The memoizer** (2026-06-02, `docs/specs/memoizer.md`). `history()` replays the
  subscription condition-algebra over the logged `value` points (passive, channel-
  only); `ensure(producer, name, until={"step": N})` serves the logged prefix on a
  hit or relaunches-to-extend and waits on a miss; the `launch_producer` seam +
  the `relaunch_if_needed` helper. `value.t` became an absolute wall-clock (the
  reader projects run-relative). The `run_id()` reuse-by-content-hash *recipe*
  (`docs/specs/run-id-recipe.md`; the re-scoped "Hasher", not a component).
- **B′ completed-opt-in** (2026-06-04, `docs/specs/completed-opt-in.md`,
  `docs/specs/preempted-vs-completed.md`). `lifecycle.stopped` became
  `{completed, error, final_step}`: `completed=True` is the worker's opt-in claim
  of intrinsic, permanent completion, and the default projects to `preempted`
  (a clean, resumable stop). `RunResult.outcome` `"stopped"` → `"preempted"`.
  (An in-place lifecycle shape change during `0.2.0.dev0` development, before any
  convention-version bump.)
- **Stop-discharge** (2026-06-09, `docs/specs/stop-discharge.md`). A `control.stop`
  is pending from append until the next `lifecycle.stopped` follows it by `seq`
  (one `stopped` discharges every pending stop); the unifying rule — *every
  control fact is live until its counter-record* — covers both stop and
  subscribe. A resumed episode never replays an answered stop.
- **The stateless observables** (2026-06-10, `docs/specs/observables.md`).
  `liveness.py` grew into `observables.py`, the stateless observer plane (pure
  body-aware folds log → view): `latest_episode` (the episode-boundary rule),
  public `progress` (the step frontier), `value_series` (the per-(name, step)
  register projection), and `vocabulary.handle_pid`.
- **The service worker** (2026-06-10, `docs/specs/service-worker.md`). One worker
  primitive, two demand durabilities: `steps(total)` runs to the launch contract's
  target; `serve()`/`retire()`/`pinned` run while leased demand exists. Expiry
  counter-records (the worker completes the subscribe/unsubscribe pair), the
  positional answer fold (`live_demand`), the careful death (the dying breath
  CAS'd against the drained log), and `await_consumed` (answer-first:
  `Nak | RunResult | None`).
- **Episode-scoped time-leases** (2026-06-11, `docs/specs/time-lease-boundary.md`).
  A time-referencing subscribe is a lease on one living worker, voided by the next
  episode's `started`; cross-episode bounds are spelled in steps, not seconds.
- **Lazy-launch** (2026-06-11, `docs/specs/lazy-launch.md`). `ensure_served` — the
  leased-demand waker beside `relaunch_if_needed` (two demand durabilities, two
  deciders); the demander's presence is both the keepalive and the waker, wasted
  spawns disciplined by the foreign-claim-scoped reap rule.
- **The store / derived-runs dissolutions** (2026-06-11, `docs/specs/store.md`,
  `docs/specs/derived-runs.md`). The relational layer ships as recipes over the
  existing basis — the rid is the run's address (content-addressed placement),
  membership is a cell pointer, provenance is the child's birth record, the index
  is derived/never-authoritative — plus one helper, `foreign_episode` (the
  producer gate's foreign half). A derived (compute-on-demand) run needs no new
  surface: it is an ordinary one-step run behind `ensure`.

### Late June 2026 — the cross-host backend

- **`PostgresChannel`** (2026-06-29, `docs/specs/channel-postgres.md`). The
  cross-host substrate backend: one shared `log` table with
  `PRIMARY KEY (run_id, seq)` as the cross-host CAS arbiter (cross-host single-
  spawn + control fall out of it, claim model unchanged), and a session advisory
  lock as a Watcher-consumed liveness capability (`EpisodeHolder`/`EpisodeProbe`,
  resolved at the Watcher's boundary into a per-run probe — never a claim
  arbiter). Ships as the optional `[postgres]` extra (`import runstate` stays
  psycopg-free). The repo's first CI workflow (`.github/workflows/tests.yml`) runs
  the suite serially against a real Postgres service, plus an import-without-
  psycopg job.

### July 2026 — the holistic review

- **`last_seq()`** (2026-07-10) — the substrate's fifth op, the CAS's read half.
  Enabled the head-first capped attach: a worker attaching to a 10⁶-envelope log
  went from 3.4 s to 1.5 ms.
- **`Worker.emit`** (2026-07-10) — the unconditional broadcast-value verb beside
  the demand-sampled `set` (the point the memoizer's series reads).
- **Typed `ensure` exceptions** (2026-07-11) — `RunFailedError` (carries the
  `RunResult` observed at raise time) and `NoProgressError`, so the caller branches
  on types rather than the message string (which stops being API).
- **`undischarged_stops`** (2026-07-11) — the stop fold's observer home, mirroring
  `live_demand`.
- The **coherence / reverse-direction audit sweep** (2026-07) — aligned the prose
  with the shipped reality; promoted the code-enforced contract into the design
  prose; closed conformance-suite coverage gaps.
- (The two July convention bumps — lifecycle-v0.3 and launcher-v0.3 — are in
  "Wire protocol versions" above.)

### 2026-07-16 — the observer clock

- **The observer clock** (`docs/specs/observer-clock.md`). Fixes a wrong-verdict
  bug: a party that attaches to a cold log had no clock, so a run dead for weeks
  read `Running`. The beacon is now dated (the lifecycle/launcher-`v0.4` bumps
  above); the `Watcher` keeps arrival time as its skew-immune witness clock and
  seeds the un-witnessed prefix from the newest beacon's own `t`; and
  `observables.last_activity` is the freshness fold (newest dated record's `t`,
  O(1)). `seq` orders / `t` measures — `t` is never an ordering key or an
  arbiter of an irreversible decision.
  (A terminal status/stop viewer lives outside this repo by design — the
  cockpit thread, `docs/backlog/cockpit.md`.)

### Release readiness (this thread, inert until the owner acts)

- Packaging metadata (`pyproject.toml` classifiers / keywords / URLs), a
  `MANIFEST.in` so the sdist carries `protocol/`, `docs/`, `examples/`,
  `CHANGELOG.md`, and `LICENSE`, this changelog, and a tag-gated PyPI release
  workflow (`.github/workflows/release.yml`) using trusted publishing, with the
  tag↔`pyproject`-version match enforced in the build job — all **inert** until
  the owner creates the PyPI project and pushes a version tag.
- The **release-and-stability-contract draft** — the
  freeze/SemVer/migration-doctrine/version-naming/schemas-in-wheel decision points,
  drafted for owner ratification. Ruled 2026-11-02 as
  `docs/specs/release-and-stability.md` (above).
- User-facing documentation: `docs/guide.md` ("Integrate your training loop in 15
  minutes") and `docs/api.md` (the public-surface reference, with a drift-guard
  test), plus a submitit/SLURM launcher *recipe* (`examples/submitit/`) and its
  design entry (`docs/backlog/submitit-launcher.md`). Then the
  **protocol-implementer's guide** (`docs/implementers-guide.md`) — the
  language-neutral conformance reference for a non-Python implementation, whose
  embedded wire examples are drift-guarded by `tests/test_implementers_guide.py`
  (the `test_schema.py` mechanic) — and two owner-gated design deliberations it
  surfaced: `docs/backlog/cross-host-claim-gate.md` and
  `docs/backlog/in-log-compaction.md` (both NOT CONVERGED).

## [0.2.0.dev0] — unreleased development version

The v0.2 redesign, implemented on `master` (2026-05-29 → 2026-05-30; design
revisions 1-4 in `docs/design-v0.2.md`). The whole post-v0.2 arc above continues
under this same version string until the owner cuts a release.

### Added

- The **topic-log substrate**: the `Channel` surface (`send` / `read` / `latest`
  / `close`, later `last_seq`) over a per-run append-only log of envelopes
  `{seq, topic, name?, request_id?, body}`; `MemoryChannel` + `SqliteChannel`;
  `Envelope`; `open_channel` (locate/open a run's channel) + `attach()`
  (worker-side, reads `RUNSTATE_*`). One backend-parametrized conformance suite.
- The **conventions** (opt-in typed shapes, pinned by JSON schemas,
  `additionalProperties: false`): cooperative-control (`control.subscribe` /
  `unsubscribe` / `stop` + the subscription condition-algebra over
  `step`/`time`/`count`), lifecycle (`started` / `heartbeat` / `stopped` / `nak`),
  launcher (`launched` / `terminated`), and the open `value`.
- The reference **`Worker`** loop (context manager + `steps(total)`).
- **Orchestration (Layer 3)**: `Launcher` / `LaunchHandle` Protocols,
  `ThreadLauncher`, `LocalLauncher`, `Watcher` (the four liveness tiers,
  `RunStatus = Running | RunResult`), `peek_terminal` / `RunResult`, `sweep`.
- The **JSON Schema stack** in `protocol/` + the conformance tests that verify
  every emitted message validates against it.

### Changed

- `RunResult` dropped the `success` boolean for a *closed* `outcome` enum plus a
  verbatim `reason` — whether a clean preemption "succeeded" is a policy the
  consumer owns, not something the producer bakes in.

## [0.1.0] — superseded, never released

The original pull-first command/event model (`Store` / `Hasher` / `Phase` /
`Preempter` / typed events), present only in the initial commit (2026-05-29) and
its design docs (`docs/design-v0.1*.md`). **Superseded by the v0.2 two-layer
redesign** (topic-log substrate + opt-in conventions) before any release; kept
only as historical rationale. Never published to PyPI.

[Unreleased]: https://github.com/GeoffChurch/runstate/commits/master
