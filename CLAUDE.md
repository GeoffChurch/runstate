# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this
repository.

## What this is

`runstate` is a **protocol** for cooperative bidirectional control
between an orchestrator and a long-running scientific worker, plus a
**reference Python implementation** of that protocol.

The protocol's value is what's unique; the Python library is one
implementation. Other-language implementations (Rust, Go, TS) are
welcome and out of scope for the current release.

v0.2 reworked the protocol from the original pull-first command/event
model into a **two-layer model**: an opinion-free **topic-log substrate**
with opt-in **conventions** on top. The design rationale lives in
`docs/design-v0.2.md` (converged) and `docs/design-v0.2-exploration.md`
(the decision trail).

## Two source-of-truth artifacts

1. **The JSON Schema stack in `protocol/`** — `envelope-v0.2.schema.json`
   (the substrate record: structure only, opaque body) plus the
   per-convention schemas (`subscription` / `launcher` / `value`-`v0.2`,
   `lifecycle`-`v0.3`), each `additionalProperties: false` and independently
   versioned. Authoritative for the wire format.
2. **`docs/design-v0.2.md`** — prose. Defines the two-layer model and
   semantics: the topic-log substrate, the conventions, the liveness
   tiers, the subscription condition-algebra, the three clocks.

When in doubt about "is this protocol-conformant?", check both. The
Python library MUST produce messages that pass the schema validators;
this is verified in `tests/test_schema.py`.

## Architecture

The substrate + opt-in conventions + reference orchestration, in
`runstate/`:

- **`channel/`** — the substrate. `Envelope` (the log record, in
  `channel/envelope.py`), `MemoryChannel` + `SqliteChannel` + the cross-host
  `PostgresChannel` (the three backends; Postgres is the optional `[postgres]`
  extra — `docs/specs/channel-postgres.md`), `open_channel` (locate/open a run's
  channel), and the opt-in capability Protocols `EpisodeHolder`/`EpisodeProbe` in
  `channel/base.py` (a backend's connection-bound liveness signal off the four-op
  base, isinstance-detected; the Watcher consumes it). A per-run append-only
  **topic log** of envelopes `{seq, topic, name?, request_id?, body}`; the
  substrate routes/indexes on the envelope and never parses `body`.
- **`vocabulary/`** — the L2 **convention vocabulary** (the typed terms another
  language reimplements to interop): `payloads.py` (frozen body dataclasses
  mirroring the schemas — `Value`/`Started`/`Heartbeat`/`Stopped`/`Nak`/
  `Launched`/`Terminated`; serialize via `asdict`, parse via `Cls(**body)`),
  `schedule.py` (the subscription **condition-algebra**: `satisfied()`,
  `Subscription`, `is_unsatisfiable()` — `from`/`every`/`until` over
  `step`/time/count), `handle.py` (portable liveness handles `local://host/pid`;
  `handle_pid` owns the parse, `resolve` the liveness probe).
- **`worker.py`** — the reference `Worker` loop (context manager + the two
  drivers: `steps(total)` runs on the launch contract's target, `serve()` on
  leased demand): drains `control.*` (positional answer fold; expiry
  counter-records), services subscriptions into `value` events, emits
  `lifecycle.*`, exposes the levels (`stop_pending`, `pinned`), and dies
  carefully (`retire()` — the death-CAS; specs/service-worker.md).
- **`observables.py`** — the **stateless observer plane**: pure body-aware
  folds log → derived view (`docs/specs/observables.md`). `peek_terminal` →
  `RunResult` (the terminal verdict; closed `outcome`, verbatim `reason`, no
  `success`), `live_episode`, `latest_episode` (the episode-boundary rule),
  `progress` (the step frontier), `value_series` (the per-(name, step)
  register projection), `live_demand` (the positional answer fold —
  unanswered subscribes). Membership test: needs a cursor or clock → it's the
  `Watcher`'s; parses a handle string → it's `vocabulary/`'s.
- **`launcher.py`** — `Launcher` / `LaunchHandle` Protocols +
  `ThreadLauncher` (in-process) + `LocalLauncher` (subprocess + `attach`;
  `reap()` + the foreign-claim-scoped reap discipline) + the two deciders:
  `relaunch_if_needed` (durable demand) and `ensure_served` (leased demand —
  lazy-launch, specs/lazy-launch.md).
- **`memoizer.py`** — `history` (replay the subscription algebra over the
  logged `value` points) + `ensure` (read-first / produce-on-miss over the
  duck-typed producer seam: `.channel`/`.run_id`/`.extend(until)` returning
  a liveness handle, never None) + `launch_producer` (the callable-worker
  factory) + `foreign_episode` (the gate's foreign half — specs/store.md
  Recipe 2; the no-progress guard is own-spawn-scoped).
- **`watcher.py`** — `Watcher`, the stateful failure detector
  (`poll`/`wait`/`wait_all`/`iter_events`/`broadcast`) + `RunStatus`
  (`Running | RunResult`).
- **`sweep.py`** — sequential multi-run helper (`sweep` + `Variant`).
- **`__init__.py`** — `attach()` (worker-side Channel factory reading
  `RUNSTATE_RUN_ID` / `RUNSTATE_CHANNEL_ROOT` / `RUNSTATE_CHANNEL_BACKEND`
  from env) + the public re-exports.

No `Orchestrator` class. There **is** a reference `Launcher` Protocol +
launchers, but they're opt-in helpers — users can spawn however they want
(subprocess, ray, submitit, hydra) and talk via the protocol. See
`examples/minimal/`.

## Style guidance

**The protocol stays opinion-free.** Anything workload-specific ("step",
"loss", "phase", "experiment") belongs in user code or in opt-in helpers
that document themselves as "one recipe; you can build your own." The
substrate never imposes message shapes — `channel.send` takes an arbitrary
`body` dict. The conventions (`control.*`, `lifecycle.*`, `launcher.*`,
`value`) are opt-in typed shapes pinned by the schemas; a worker that opts
out composes its own loop from `send`/`read`/`latest` + the liveness tiers.

**Helpers earn their place.** Before adding a new typed convention message,
ask: does the protocol itself need to recognize it (the way
`lifecycle.stopped` / `launcher.terminated` drive `peek_terminal`, or
`control.*` drives the worker loop)? If not, it's a user-defined dict under
`topic="value"` — keep it out of the conventions.

**The `additionalProperties: false` in the schemas is load-bearing.** It
means a future protocol version can't silently add fields; adding a field
to a well-known body is a deliberate convention-version bump. Each
convention schema is versioned on its own timeline.

## Design rigor — the orthonormal-basis rubric

Every layer's primitives should be a **minimal, canonical, orthogonal basis**
for the feature space they serve — chosen with the same care the substrate
was. (Properties that hold under audit: the append-only log is **initial**
among communication views, under full retention; the closed `RunResult.outcome`
enum is the **canonical projection** of the liveness tiers — which is why there
is no `success` bool; heartbeat-staleness **subsumes** a substrate liveness
lease. Two tempting claims that do *not* hold of the *shipped* design, kept as
cautionary examples: the heartbeat is **not** Unit/terminal — it is deliberately
*enriched* to `{step, consumed_seq}` to amortize the subscribe-ack; and the
condition-algebra is the **free** term algebra, deliberately *not* a normal
form, since conditions are never compared or hashed.) The payoff of getting the
basis right is *serendipity* — features that compose for free. When proposing or
reviewing any primitive (a convention message, a typed body, an operation),
test it against:

1. **Independence (necessity).** Could it be derived by composing the
   others? If so it's redundant — name what subsumes it. (Watch for one
   primitive silently doing another's job — e.g. heartbeat-staleness
   obviating a separate liveness signal.)
2. **Spanning (sufficiency).** Name an in-scope feature the set *cannot*
   express by composition (a missing basis vector) — and any feature it
   expresses that's *out of scope* (opinion creep).
3. **Canonical form.** Among equivalent ways to provide it, is this the one
   with a universal property / normal form / least arbitrary content? Flag
   arbitrary, over-specified, or coordinate-dependent choices.
4. **Orthogonality.** Does any pair overlap in the concern it encodes? Each
   should carry exactly one. (Model: the launcher-vs-lifecycle "two
   viewpoints" split keeps self-report and external-report orthogonal.)
5. **Serendipity (payoff, and its absence as a smell).** Where does the
   basis make a feature fall out *for free* (run-absolute step from one log;
   log-as-cache)? Where you'd expect synergy and don't see it, suspect the
   basis is off.

Meta-constraint (overrides 1–5): the substrate stays **opinion-free** and
conventions are **opt-in** typed shapes pinned by `additionalProperties:false`.
A primitive that bakes a workload opinion fails regardless of how well it
scores above.

## Test commands

```bash
pip install -e .                    # install editable
pip install -e .[test]              # + jsonschema for the schema tests
pytest tests/                       # run all tests (~700, ~9s; +Postgres if a DSN is set)
pytest tests/test_channel.py -v     # one module
pytest tests/test_schema.py -v      # emitted messages conform to the schema stack
```

The Channel conformance tests and the substrate-level convention tests are
parametrized over the backends (`memory` + `sqlite` always, and `postgres` when
`RUNSTATE_TEST_PG_DSN` points at a server); each must pass independently. The
shared-table Postgres fixtures mint a uuid run_id per test and run **serial**
(xdist-unsafe). `tests/test_schema.py` is skipped if `jsonschema` is absent.

## Common operations

**Run the minimal example:**
```bash
python examples/minimal/driver.py
```
Spawns the worker as a subprocess via `LocalLauncher`, subscribes to its
`loss` each step, streams the `value` events through `Watcher.wait(on_event=…)`,
would send a cooperative `control.stop` if loss diverged (it doesn't here),
and prints the terminal `RunResult`. Demonstrates the full surface.

**Validate that emitted messages stay schema-conformant:**
`tests/test_schema.py` drives a scenario that emits every reserved topic,
harvests the log, and validates each envelope against the envelope schema +
its convention schema. Extend the scenario (or the negative cases) when you
add or change a convention body.

**Add a new Channel backend:**
1. Implement the Channel surface in `runstate/channel/<name>.py`
   (import `Envelope` from `.envelope`, not the package `__init__`).
2. Add a `"<name>"` branch in `runstate/channel/__init__.py:open_channel`.
3. Parametrize the conformance tests over the new backend in
   `tests/conftest.py`.
4. Declare the backend's concurrency tier (`in_process` / `cross_process` /
   `cross_host`) in `tests/conftest.py`'s `_MAX_TIER` ladder, so the
   tier-gated concurrency suite covers it up to that tier.
5. All existing Channel conformance tests must pass against it unchanged.

## Where to put new ideas

See `docs/README.md` for the documentation map. In short: forward-looking
ideas go in `docs/backlog/` (one-liners in `index.md` or standalone files
for elaboration). Backlog entries are **living documents** — they evolve
as the idea is investigated, accumulating alternatives, prerequisites,
and open questions. Refuted ideas with diagnosis move to `docs/dead_ends/`
(parallel structure).

**To take on deferred/upcoming work, start at `docs/backlog/index.md`** — its
"Start here" gives the reading order and the big-ticket threads (run-episodes;
the relational layer, DISSOLVED 2026-06-11 into `docs/specs/store.md` with
`mycooc-adoption.md` as the validating-consumer ledger and the mycooc wiring
plan as the remaining work; and the deferred design-§12 items mirrored there).

## Scope snapshot

**Shipped in v0.2 (this effort):**
- Topic-log substrate: Channel surface + `MemoryChannel` + `SqliteChannel`
  + `Envelope` + `open_channel`; thread-safe in-process sharing.
- The conventions: cooperative-control (`control.subscribe`/`unsubscribe`/
  `stop` + the condition-algebra), lifecycle (started/heartbeat/stopped/
  nak), launcher (launched/terminated), `value`.
- The reference `Worker` loop + `attach()`.
- Orchestration (Layer 3): `Launcher`/`LaunchHandle` Protocols,
  `ThreadLauncher`, `LocalLauncher`, `Watcher` (4 liveness tiers,
  `RunStatus`), `peek_terminal`/`RunResult`, `sweep`.
- The JSON Schema stack + conformance tests.

**Shipped since v0.2:**
- The cross-host **`PostgresChannel`** backend (`docs/specs/channel-postgres.md`):
  one shared `log` table, the CAS (`PRIMARY KEY (run_id, seq)`) as the cross-host
  claim arbiter (cross-host single-spawn + control fall out of it, claim model
  unchanged), and a session advisory lock as a Watcher-consumed liveness capability
  (`EpisodeHolder`/`EpisodeProbe`, resolved at the Watcher's boundary into a per-run
  probe — never a claim arbiter). Optional `[postgres]` extra; the repo's first CI
  workflow runs it against a Postgres service.

**Deferred (v0.3+)** (see `docs/backlog/index.md`):
- ~~Store Protocol + backends~~ — **DISSOLVED 2026-06-11**
  (`docs/specs/store.md`): the relational layer ships as recipes over the
  existing basis (rid-as-address / content-addressed placement; cell
  pointers; the child's birth record; a derived never-authoritative
  index) + one helper (`foreign_episode`). Remaining: the mycooc wiring
  plan (the cell/run split migration).
- A `run_id()` recipe — **shipped** (`docs/specs/run-id-recipe.md`; the
  re-scoped "Hasher", *not* a component): content-addressable identity is
  a substrate affordance via caller-chosen `run_id`, and under placement
  the rid is also the run's address; the input-partition policy is
  workload-specific.
- Pause / Resume / Snapshot / Reconfigure commands.
- The webapp viewer (`docs/backlog/webapp-viewer.md`).
- Open §12 implementation items still on the design's list
  (cursor-persistence efficiency, multi-orchestrator attribution,
  author/provenance; lazy-launch closed 2026-06-11, home-level GC
  recipe'd same day).

**Likely never in the core library:**
- *Full* process management. The reference launchers are deliberately
  thin (a thread; a `subprocess.Popen` + `attach`) — users with ray /
  submitit / hydra use those and talk via the protocol. The library
  transports messages, not processes.
- Run config management (use Hydra / OmegaConf / argparse).

**Long-term ambition (on the horizon, not yet):**
- **Visualization protocols.** runstate ships a control-plane protocol; a
  data-plane protocol (richer `value` events: histograms, images, audio,
  tensors) plus a viewer-side protocol (how a UI discovers runs,
  subscribes, renders) would let a **separate viz project on runstate** be a
  one-stop shop instead of shipping alongside wandb / MLflow / TensorBoard. (`Watcher`'s
  `RunStatus`/`Running` already gestures at the viewer-side fold.) See
  `docs/backlog/visualization-story.md`. The bar: only ship this as a
  coherent protocol story, not "another tracking tool."
- **Companion webapp / TUI** built on those protocols.

The discipline: visualization gets its OWN protocol in a **separate project**
(not runstate's `protocol/`), distinct from the cooperative-control protocol —
runstate stays the minimal control protocol + the substrate the data plane rides
on. Compose, don't conflate.
