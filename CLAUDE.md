# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this
repository.

## What this is

`runstate` is a **protocol** for cooperative bidirectional control
between an orchestrator and a long-running scientific worker, plus a
**reference Python implementation** of that protocol.

The protocol's value is what's unique; the Python library is one
implementation. Other-language implementations (Rust, Go, TS) are
welcome and out-of-scope for v0.1 to ship.

## Two source-of-truth artifacts

1. **`protocol/messages-v0.1.schema.json`** — the JSON Schema. Defines
   the wire format. Authoritative for what valid messages look like.
2. **`protocol/spec.md`** — prose. Defines semantics: on-disk layout,
   ack rules, cooperative-preempt discipline, role conventions.

When in doubt about "is this protocol-conformant?", check both. The
Python library MUST produce messages that pass the schema validator;
this is verified in `tests/test_schema.py`.

## Architecture

Four modules in `runstate/`:

- **`channel/`** — Channel Protocol + FileChannel + SqliteChannel. The
  substrate: durable per-run bidirectional dict transport.
- **`control.py`** — typed orchestrator→worker commands. StopNow,
  StopAtStep, parse(), send_*, Checker (worker-side deferred-preempt
  state holder), check() (functional convenience).
- **`events.py`** — typed worker→orchestrator events. Progress,
  Stopped, Ack, parse(), send, progress(), stopped().
- **`__init__.py`** — re-exports + `attach()` (worker-side Channel
  factory that reads RUNSTATE_RUN_ID / RUNSTATE_CHANNEL_ROOT /
  RUNSTATE_CHANNEL_BACKEND from env).

No `Orchestrator` class. No `Launcher` Protocol. Users spawn worker
processes however they want (subprocess, ray, submitit, hydra) and use
the protocol to talk to them. See `examples/minimal/driver.py`.

## Style guidance

**The protocol stays opinion-free.** Anything that's workload-specific
("step", "loss", "phase", "experiment") belongs in user code or in
opt-in helpers that document themselves as "one recipe; you can build
your own." The Channel substrate never imposes message shapes;
`control.py` and `events.py` provide typed shapes but they're opt-in
(users can always `channel.send(arbitrary_dict)`).

**Helpers earn their place.** If you're adding a new typed command or
event to `control.py` / `events.py`, ask: does this need to be
recognized by the orchestrator's protocol-level inference (like
`Stopped` and `Ack` are)? If not, it's a user-defined dict — keep it
out of the helpers.

**The `additionalProperties: false` in the JSON Schema is load-bearing.**
It means future protocol versions can't silently add fields. When
adding a field, you're proposing a protocol version bump.

## Test commands

```bash
pip install -e .                    # install editable
pip install -e .[test]              # + jsonschema for schema tests
pytest tests/                       # run all tests (~80, sub-1s)
pytest tests/test_channel.py -v     # one module
pytest tests/test_schema.py -v      # verifies dataclass output conforms to schema
```

The test suite is parametrized over both Channel backends (file +
sqlite) for the Channel and control/events conformance tests; both
must pass independently.

## Common operations

**Run the minimal example:**
```bash
python examples/minimal/driver.py
```
Spawns the worker, prints its progress events, sends a StopNow if loss
diverges (it doesn't in this example), drains the final Stopped event.
Demonstrates the full protocol surface.

**Validate that a new typed dataclass conforms to the schema:**
Add a test in `tests/test_schema.py` calling
`event_validator.validate(asdict(new_event_instance))` or
`command_validator.validate(asdict(new_command_instance))`.

**Add a new Channel backend:**
1. Implement the Channel Protocol in `runstate/channel/<name>.py`
2. Add a `"<name>"` literal in `runstate/channel/__init__.py:open_channel`
3. Parametrize tests over the new backend in `tests/conftest.py`
4. Update the test suite — all existing Channel conformance tests must
   pass against the new backend with no changes.

## Where to put new ideas

See `docs/README.md` for the documentation map. In short: forward-looking
ideas go in `docs/backlog/` (one-liners in `index.md` or standalone files
for elaboration). Backlog entries are **living documents** — they evolve
as the idea is investigated, accumulating alternatives, prerequisites,
and open questions. Refuted ideas with diagnosis move to `docs/dead_ends/`
(parallel structure).

## v0.1 vs v0.2 scope snapshot

**v0.1 (this release):**
- Channel Protocol + FileChannel + SqliteChannel
- control + events typed helpers (StopNow, StopAtStep, Progress,
  Stopped, Ack)
- `attach()` worker-side convenience
- JSON Schema + prose spec

**Deferred to v0.2** (see `docs/backlog/index.md`):
- Store Protocol + backends (relational metadata; many-to-many
  Run × Experiment membership)
- Hasher Protocol + DefaultHasher (content-addressable input
  fingerprinting)
- Reuse-by-hash via Store
- Multi-run sweep helpers
- Launcher Protocol + LocalLauncher / ThreadLauncher
- Pause / Resume / Snapshot / Reconfigure commands
- The webapp viewer (`docs/backlog/webapp-viewer.md`)

**Likely never in the core library:**
- Process spawning (users use subprocess.Popen, ray, submitit, etc.;
  the library transports messages, not processes)
- Run config management (use Hydra / OmegaConf / argparse)

**Long-term ambition (NOT v0.1, NOT v0.2, but on the horizon):**
- **Visualization protocols.** runstate already ships a control-plane
  protocol; a data-plane protocol (richer Progress events: histograms,
  images, audio, tensors) plus a viewer-side protocol (how a UI
  discovers runs, subscribes to updates, renders artifacts) would let
  runstate be a one-stop shop instead of always shipping alongside
  wandb / MLflow / TensorBoard. See `docs/backlog/visualization-story.md`.
  The bar is: only ship this if we can do it as a coherent protocol
  story, not just "another tracking tool."
- **Companion webapp / TUI** built on top of those protocols.

The discipline: visualization gets its OWN protocol (in `protocol/`),
distinct from the cooperative-control protocol. Compose, don't
conflate.
