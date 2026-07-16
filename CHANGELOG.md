# Changelog

All notable changes to runstate are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
runstate has **not yet cut a public release**: the package on `master` is
`0.2.0.dev0`, while the docs describe the shipped arc as "v0.3" and the wire
conventions are already at `v0.4`. That three-way version-naming tension, and
what SemVer means against per-convention wire versions, is an owner decision
drafted (not ruled) in
[`docs/backlog/release-and-stability-contract.md`](docs/backlog/release-and-stability-contract.md).

Reconstructed from the repo's own records — `docs/design-v0.2.md` (revision
history), the dated status lines in `docs/specs/*`, and `git log master`. Not a
substitute for those; a reading order over them.

## [Unreleased]

The post-v0.2 arc, landed on `master`, not yet released. Grouped by the thread
that produced each change.

### Wire protocol versions (breaking)

Each convention schema in `protocol/` is versioned on its own timeline and is
`additionalProperties: false`, so any field change is a deliberate version bump
(never a silent addition). The envelope (`envelope-v0.2`) and the
subscription/value conventions (`subscription-v0.2`, `value-v0.2`) are unchanged
since v0.2. The lifecycle and launcher conventions each bumped twice:

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
  renamed to `t` (`docs/specs/observer-clock.md`). Existing logs migrate offline
  via `scripts/migrate_observer_clock_v0_4.py` (quiescence-gated). This script is
  **retained** in `scripts/`, not deleted — the point at which the repo's
  schema-file-deletion / no-compat doctrine has to change is one of the stability
  contract's open decisions.

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

### 2026-07-16 — the observer clock + the CLI

- **The observer clock** (`docs/specs/observer-clock.md`). Fixes a wrong-verdict
  bug: a party that attaches to a cold log had no clock, so a run dead for weeks
  read `Running`. The beacon is now dated (the lifecycle/launcher-`v0.4` bumps
  above); the `Watcher` keeps arrival time as its skew-immune witness clock and
  seeds the un-witnessed prefix from the newest beacon's own `t`; and
  `observables.last_activity` is the freshness fold (newest dated record's `t`,
  O(1)). `seq` orders / `t` measures — `t` is never an ordering key or an
  arbiter of an irreversible decision.
- **The `runstate` CLI** (`runstate/cli.py`, the `runstate` console script). Two
  read-mostly commands over a run's sqlite log: `runstate status <root>` (a
  snapshot table — verdict / progress / freshness, the last powered by the
  observer clock; discovers both the flat and Recipe-1 sharded layouts) and
  `runstate stop <root> <run_id> [--wait N]` (a cooperative `control.stop`, armed
  for the next episode if the run is down). A tool, not API — not in
  `runstate.__all__`; deliberately not a daemon or live viewer.

### Release readiness (this thread, inert until the owner acts)

- Packaging metadata (`pyproject.toml` classifiers / keywords / URLs), a
  `MANIFEST.in` so the sdist carries `protocol/`, `docs/`, `examples/`, `scripts/`,
  `CHANGELOG.md`, and `LICENSE`, this changelog, and a tag-gated PyPI release
  workflow (`.github/workflows/release.yml`) using trusted publishing — all
  **inert** until the owner creates the PyPI project and pushes a version tag.
- The **release-and-stability-contract draft**
  (`docs/backlog/release-and-stability-contract.md`, `PROPOSED`) — the
  freeze/SemVer/migration-doctrine/version-naming/schemas-in-wheel decision points,
  drafted for owner ratification. Nothing there is policy until ruled.
- User-facing documentation: `docs/guide.md` ("Integrate your training loop in 15
  minutes") and `docs/api.md` (the public-surface reference, with a drift-guard
  test), plus a submitit/SLURM launcher *recipe* (`examples/submitit/`) and its
  design entry (`docs/backlog/submitit-launcher.md`).

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
