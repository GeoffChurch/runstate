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
   per-convention schemas (`subscription` / `lifecycle` / `launcher` /
   `value`-`v0.2`), each `additionalProperties: false` and independently
   versioned. Authoritative for the wire format.
   (`messages-v0.1.schema.json` is the superseded single-schema.)
2. **`docs/design-v0.2.md`** — prose. Defines the two-layer model and
   semantics: the topic-log substrate, the conventions, the liveness
   tiers, the subscription condition-algebra, the three clocks.
   (`protocol/spec.md` is the superseded v0.1 prose.)

When in doubt about "is this protocol-conformant?", check both. The
Python library MUST produce messages that pass the schema validators;
this is verified in `tests/test_schema.py`.

## Architecture

The substrate + opt-in conventions + reference orchestration, in
`runstate/`:

- **`channel/`** — the substrate. `Envelope` (the log record, in
  `channel/envelope.py`), `MemoryChannel` + `SqliteChannel` (the two
  backends), `open_channel` (locate/open a run's channel). A per-run
  append-only **topic log** of envelopes `{seq, topic, name?,
  request_id?, body}`; the substrate routes/indexes on the envelope and
  never parses `body`.
- **`vocabulary/`** — the L2 **convention vocabulary** (the typed terms another
  language reimplements to interop): `payloads.py` (frozen body dataclasses
  mirroring the schemas — `Value`/`Started`/`Heartbeat`/`Stopped`/`Nak`/
  `Launched`/`Terminated`; serialize via `asdict`, parse via `Cls(**body)`),
  `schedule.py` (the subscription **condition-algebra**: `satisfied()`,
  `Subscription`, `is_unsatisfiable()` — `from`/`every`/`until` over
  `step`/time/count), `handle.py` (portable liveness handles `local://host/pid`).
- **`worker.py`** — the reference `Worker` loop (context manager +
  `steps()`): drains `control.*`, services subscriptions into `value`
  events, emits `lifecycle.*` (started / heartbeat / stopped / nak).
- **`liveness.py`** — `RunResult` (the terminal verdict; closed
  `outcome`, verbatim `reason`, no `success`) + `peek_terminal` (the
  record-based verdict).
- **`launcher.py`** — `Launcher` / `LaunchHandle` Protocols +
  `ThreadLauncher` (in-process) + `LocalLauncher` (subprocess + `attach`).
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

## Test commands

```bash
pip install -e .                    # install editable
pip install -e .[test]              # + jsonschema for the schema tests
pytest tests/                       # run all tests (~130, sub-1s)
pytest tests/test_channel.py -v     # one module
pytest tests/test_schema.py -v      # emitted messages conform to the schema stack
```

The Channel conformance tests and the substrate-level convention tests are
parametrized over **both** backends (`memory` + `sqlite`); both must pass
independently. `tests/test_schema.py` is skipped if `jsonschema` is absent.

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
4. All existing Channel conformance tests must pass against it unchanged.

## Where to put new ideas

See `docs/README.md` for the documentation map. In short: forward-looking
ideas go in `docs/backlog/` (one-liners in `index.md` or standalone files
for elaboration). Backlog entries are **living documents** — they evolve
as the idea is investigated, accumulating alternatives, prerequisites,
and open questions. Refuted ideas with diagnosis move to `docs/dead_ends/`
(parallel structure).

**To take on deferred/upcoming work, start at `docs/backlog/index.md`** — its
"Start here" gives the reading order and the big-ticket threads (run-episodes,
the Store relational layer + `mycooc-adoption.md` — the "Hasher" re-scoped to a
`run_id()` recipe — and the deferred design-§12 items mirrored there).

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

**Deferred (v0.3+)** (see `docs/backlog/index.md`):
- Store Protocol + backends (relational metadata; many-to-many
  Run × Experiment membership; the cross-run reuse-by-hash query).
- A `run_id()` recipe (the re-scoped "Hasher" — *not* a component):
  content-addressable identity is already a substrate affordance via
  caller-chosen `run_id`; the input-partition policy is workload-specific.
- Pause / Resume / Snapshot / Reconfigure commands.
- The webapp viewer (`docs/backlog/webapp-viewer.md`).
- Open §12 implementation items still on the design's list (lazy-launch
  double-spawn race, cursor-persistence mechanics, multi-orchestrator vs
  `latest(control.*)`, author/provenance).

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
  subscribes, renders) would let runstate be a one-stop shop instead of
  shipping alongside wandb / MLflow / TensorBoard. (`Watcher`'s
  `RunStatus`/`Running` already gestures at the viewer-side fold.) See
  `docs/backlog/visualization-story.md`. The bar: only ship this as a
  coherent protocol story, not "another tracking tool."
- **Companion webapp / TUI** built on those protocols.

The discipline: visualization gets its OWN protocol (in `protocol/`),
distinct from the cooperative-control protocol. Compose, don't conflate.
