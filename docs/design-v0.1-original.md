# runstate v0.1 — Design

**Status:** approved scope, ready for implementation plan
**Date:** 2026-05-27

## Goal

`runstate` is the orchestration layer between sweep generators (Hydra, Optuna) and trackers (wandb, MLflow). It owns the responsibilities that fall through the gap in the existing ML tooling stack:

- **Dispatch:** spawning and observing worker processes
- **Cooperative IPC:** bidirectional message channel between an orchestrator and a long-running worker, with safe-point semantics
- **Content-addressable reuse:** automatic detection that "this config + this code state + this seed has already been run" and reuse of the prior result
- **Relational metadata:** durable record of runs and their membership in experiments

The library is **complementary** to existing tools, not competitive:
- Trackers (wandb, MLflow, neptune) are passive metric observers — they remain useful for visualization
- Sweep generators (Hydra, Optuna) produce configs — they remain useful for parameter exploration
- runstate sits between, owning the orchestration loop and the IPC channel

## Positioning

```
   sweep generator (Hydra / Optuna)
            │
            ▼
      runstate orchestrator
   ┌────────┴────────┐
   │                 │
Channel (IPC)    Launcher
   │                 │
   ▼                 ▼
 worker script ──→ tracker (wandb)
   │                 │
   │                 ▼
   │            web UI, plots
   ▼
 your training loop
```

The worker reports metrics to the tracker (data-plane) AND to the runstate Channel (control-plane). The two are independent — the worker uses both simultaneously.

## Architecture

Five modules; each is independently importable.

```
runstate/
  __init__.py
  types.py          # RunId, Phase, Timestamp — shared types
  channel.py        # Channel Protocol + FileChannel + SqliteChannel
  store.py          # Store Protocol + FileStore + SqliteStore
  fingerprint.py    # Hasher Protocol + DefaultHasher
  launcher.py       # Launcher / ProcessHandle Protocols + LocalLauncher
  orchestrate.py    # minimal v0.1 orchestrator (uses the four protocols)
```

Five protocols total: `Channel`, `Store`, `Hasher`, `Launcher`, `ProcessHandle`. Each is small (5-10 methods). The orchestrator is the only place that uses all of them at once; modules don't import each other (except `orchestrate.py` which composes them, and `types.py` which provides shared types).

## Shared types (`types.py`)

```python
from typing import NewType, Literal
from enum import StrEnum

RunId = NewType('RunId', str)
Timestamp = float  # Unix epoch seconds

class Phase(StrEnum):
    PENDING = "pending"
    LOADING = "loading"
    TRAINING = "training"
    SAVING = "saving"
    DONE = "done"
    FAILED = "failed"
```

Rationale: small, opinionated shared vocabulary. `Phase` is a sensible default for the most common worker lifecycle; users with different lifecycles can use string phases not in this enum (the Channel transports `dict` payloads; `Phase` values are just suggested constants).

## Channel (`channel.py`)

Bidirectional durable per-run message transport between an orchestrator and a worker.

### Protocol

```python
class Channel(Protocol):
    """Bidirectional message channel for one run."""

    def send(self, message: dict) -> None:
        """Send a message. Durable: survives process crash. Atomic write."""

    def recv(self, timeout: float | None = None) -> dict | None:
        """Receive the next unread message. Returns None on timeout."""

    def recv_history(self) -> Iterator[dict]:
        """Iterate all messages ever sent on this channel, oldest first."""

    def close(self) -> None:
        """Release any resources (file handles, DB connections)."""


class ChannelFactory(Protocol):
    """Opens Channels by run identity. Role determines send/recv direction."""

    def open(
        self,
        run_id: RunId,
        role: Literal['worker', 'orchestrator'],
    ) -> Channel:
        ...
```

### Semantics

- Messages are JSON-serializable dicts. The Channel doesn't impose schema beyond that.
- Worker role's `send` goes to the orchestrator; `recv` reads from orchestrator. Vice versa for orchestrator role.
- `send` is durable — survives process crash. The receiver picks up unread messages on reconnect.
- `recv` returns `None` on timeout. Implementations may poll (file) or block (SQLite WAL on `last_inserted_id` query).
- `recv_history` is for debugging and event-sourcing patterns. Returns ALL messages regardless of consumed status.

### Default implementations

**`FileChannel`** — zero deps.
- One directory per run: `<run_root>/messages/`
- Send direction-specific files: `to_worker/<seq>.json` and `to_orchestrator/<seq>.json`
- Atomic write via temp-file + rename
- recv polls directory mtime + scans for unread `<seq>.json` files

**`SqliteChannel`** — stdlib `sqlite3`.
- One DB per run: `<run_root>/run.db`
- Table: `messages(id INTEGER PRIMARY KEY, direction TEXT, payload TEXT, created_at REAL, consumed_at REAL)`
- WAL mode for concurrent reads with one writer
- recv uses `last_inserted_id` polling

Both back the same Protocol; the choice is a one-line factory swap.

## Store (`store.py`)

Relational metadata for runs and experiments. Many-to-many membership.

### Schema (logical, not tied to backend)

```
Run(
  hash: str,                     # content-addressable identifier (from Hasher)
  config: JSON,                  # serialized config dict
  seed: int | None,              # random seed (None if not applicable)
  status: Phase,                 # current phase
  created_at: Timestamp,
  updated_at: Timestamp,
  run_dir: str,                  # filesystem path to this run's artifacts
)

Experiment(
  name: str,                     # human-friendly identifier
  created_at: Timestamp,
)

InExperiment(
  run_hash: str,                 # foreign key → Run
  experiment_name: str,          # foreign key → Experiment
)                                # primary key: (run_hash, experiment_name)
```

### Protocol

```python
class Store(Protocol):
    """Relational metadata for runs and experiments."""

    def get_run(self, hash: str) -> Run | None: ...
    def upsert_run(self, run: Run) -> None: ...
    def update_run_status(self, hash: str, status: Phase) -> None: ...
    def find_runs(self, **filters) -> Iterator[Run]: ...

    def create_experiment(self, name: str) -> None: ...
    def add_to_experiment(self, run_hash: str, experiment: str) -> None: ...
    def remove_from_experiment(self, run_hash: str, experiment: str) -> None: ...
    def experiments_of(self, run_hash: str) -> list[str]: ...
    def runs_in(self, experiment: str) -> Iterator[Run]: ...

    def runs_without_experiment(self) -> Iterator[Run]: ...
    """For pruning."""
```

### Default implementations

**`FileStore`** — zero deps.
- `<store_root>/runs/<hash>/meta.json` — one file per Run row
- `<store_root>/experiments/<name>/runs.txt` — one line per run hash
- Concurrent writes protected by `flock()` on per-file basis (via `portalocker` or `fcntl`)
- Cross-run queries are O(N) iteration

**`SqliteStore`** — stdlib `sqlite3`.
- Central `<store_root>/store.db` with `Run`, `Experiment`, `InExperiment` tables
- Indexes on `Run.hash`, `Run.status`, `InExperiment.run_hash`, `InExperiment.experiment_name`
- WAL mode for concurrent read

Both pass the same conformance test suite.

## Fingerprint (`fingerprint.py`)

Content-addressable hashing — turns "config + code + seed" into a deterministic run ID.

### Protocol

```python
class Hasher(Protocol):
    """Produces deterministic run identifiers from inputs."""

    def run_id(
        self,
        config: dict,
        repo_root: Path,
        seed: int | None = None,
    ) -> RunId:
        """Return a deterministic identifier for these inputs."""
```

### Default implementation

```python
class DefaultHasher:
    """Hashes config + git state + file content + seed.

    file_patterns specifies which files contribute to the hash.
    Defaults to '*.py' under repo_root (excluding common noise dirs).
    """

    def __init__(
        self,
        file_patterns: list[str] = ('*.py',),
        exclude_dirs: list[str] = ('docs', 'experiments', 'outputs',
                                   '__pycache__', '.git'),
        algorithm: str = 'sha256',
    ): ...

    def run_id(self, config, repo_root, seed=None) -> RunId:
        h = hashlib.new(self.algorithm)
        h.update(canonical_json(config).encode())
        h.update(git_commit_hash(repo_root).encode())
        h.update(b'dirty:' + git_dirty_state(repo_root).encode())
        for path in sorted(self._iter_files(repo_root)):
            h.update(str(path.relative_to(repo_root)).encode())
            h.update(path.read_bytes())
        if seed is not None:
            h.update(f'seed={seed}'.encode())
        return RunId(h.hexdigest()[:16])
```

The protocol decouples policy ("what counts as an input") from mechanism. Users who want different policies (e.g., include data file hashes, exclude config-but-include-only-certain-keys) write their own `Hasher`.

`xxhash` as an optional faster algorithm is deferred to v0.2.

## Launcher (`launcher.py`)

Process spawning + observation. Pluggable backends.

### Protocols

```python
class Launcher(Protocol):
    """Spawns processes and returns observable handles."""

    def launch(
        self,
        cmd: list[str],
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessHandle: ...


class ProcessHandle(Protocol):
    """Observable handle to a running process."""

    pid: Any                       # backend-defined identity
    def poll(self) -> int | None:  # returncode or None if still running
    def terminate(self) -> None:   # request graceful shutdown (SIGTERM-equivalent)
    def kill(self) -> None:        # force-kill (SIGKILL-equivalent)
    def wait(self, timeout: float | None = None) -> int:  # block until done
```

### Default implementation

**`LocalLauncher`** — wraps `subprocess.Popen`. ~30 LOC. Returns a `LocalProcessHandle` that delegates to the underlying `Popen`.

`SubmititLauncher`, `RayLauncher`, etc. are out of v0.1; live in optional packages.

## Orchestrate (`orchestrate.py`)

Minimal v0.1 orchestrator. Composes the four protocols into a usable API.

### v0.1 API

```python
@dataclass
class Orchestrator:
    channel_factory: ChannelFactory
    store: Store
    hasher: Hasher
    launcher: Launcher
    runs_root: Path                # where run dirs are created

    def run(
        self,
        config: dict,
        cmd_template: list[str],   # e.g., ["python", "train.py", "{config_path}"]
        repo_root: Path,
        seed: int | None = None,
        experiment: str | None = None,
    ) -> RunId:
        """Dispatch a single config. Auto-reuses by fingerprint.

        Returns the RunId. If a matching completed run exists, returns
        its hash without re-running.
        """
        run_id = self.hasher.run_id(config, repo_root, seed)

        # Reuse check
        existing = self.store.get_run(run_id)
        if existing and existing.status == Phase.DONE:
            if experiment:
                self.store.add_to_experiment(run_id, experiment)
            return run_id

        # Dispatch
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        config_path = self._write_config(run_dir, config)
        cmd = [arg.format(config_path=str(config_path)) for arg in cmd_template]

        new_run = Run(hash=run_id, config=config, seed=seed,
                      status=Phase.PENDING, ...)
        self.store.upsert_run(new_run)

        proc = self.launcher.launch(cmd, cwd=run_dir)
        # ... open Channel, observe phase changes, update Store as messages
        # arrive, wait for process exit

        if experiment:
            self.store.add_to_experiment(run_id, experiment)
        return run_id

    def run_sweep(
        self,
        configs: Iterable[dict],
        cmd_template: list[str],
        repo_root: Path,
        seeds: list[int] | None = None,
        experiment: str | None = None,
    ) -> list[RunId]:
        """Sequential sweep dispatch over (config × seed) cross product.

        v0.1: sequential only. No parallelism.
        """
        ids = []
        for config in configs:
            for seed in (seeds or [None]):
                ids.append(self.run(config, cmd_template, repo_root,
                                    seed=seed, experiment=experiment))
        return ids
```

### What v0.1's orchestrator does NOT do

- **No fire-and-forget background worker.** `run()` blocks until the dispatched process exits.
- **No parallel dispatch.** `run_sweep` is sequential.
- **No resume budget.** Runs that exit non-DONE are FAILED; user re-runs manually.
- **No smoke gate.** Every variant in a sweep is attempted regardless of earlier failures.
- **No CLI.** Library only.

These features are well-defined extensions for v0.2+.

## Worker-side API

Workers use `ChannelFactory` to attach to their channel. The library provides:

```python
def attach_to_channel(run_id: RunId, root: Path | None = None) -> Channel:
    """Convenience: returns a worker-role Channel.

    By default reads RUN_ID and RUN_ROOT from environment variables set
    by the orchestrator."""
```

The orchestrator passes `RUN_ID` and `RUN_ROOT` via the env when launching the worker. The worker calls `attach_to_channel()` with no args and starts using the channel.

Inside the training loop, the worker pattern is:

```python
ch = runstate.attach_to_channel()
ch.send({'type': 'PhaseChange', 'phase': 'training'})

for step in range(max_steps):
    do_one_step()
    ch.send({'type': 'Progress', 'step': step, 'loss': loss})

    # Cooperative-preempt safe point
    msg = ch.recv(timeout=0)  # non-blocking
    if msg and msg['type'] == 'StopAtStep' and step >= msg['at']:
        checkpoint_and_exit()
    if msg and msg['type'] == 'StopNow':
        checkpoint_and_exit()
```

The user defines the protocol (what `type` values are valid). runstate transports the dicts.

## Testing

### Conformance tests

A single parametrized test suite runs against every backend:

```python
@pytest.fixture(params=['file', 'sqlite'])
def channel_factory(request, tmp_path):
    if request.param == 'file':
        return FileChannelFactory(tmp_path)
    if request.param == 'sqlite':
        return SqliteChannelFactory(tmp_path)

def test_send_recv_roundtrip(channel_factory):
    ch_w = channel_factory.open('test-run', role='worker')
    ch_o = channel_factory.open('test-run', role='orchestrator')
    ch_w.send({'a': 1})
    assert ch_o.recv() == {'a': 1}
    # ... etc
```

Every Protocol method gets a test. Every backend must pass every test.

### Coverage targets

- All Protocol methods covered for both Channel and Store backends
- Hasher: golden-output tests (same inputs → same hash; different inputs → different hash)
- Launcher: smoke test that LocalLauncher can spawn a subprocess and observe its exit
- Orchestrator: integration test that exercises full dispatch path with a toy worker script

### CI

GitHub Actions running pytest on Python 3.11, 3.12. Out-of-scope for v0.1 implementation; ship the workflow file but it can be a stub.

## Out of v0.1 scope (deferred to later versions)

- **Postgres backend** for Channel and Store. v1.x as optional `runstate-postgres` package.
- **Fire-and-forget background worker.** Orchestrator detaching from the worker. v0.2.
- **Resume budgets** (`max_steps_per_run` semantics). v0.2.
- **Smoke gate.** v0.2.
- **`--status` CLI / table renderer.** v0.2.
- **Hydra adapter** (`runstate-hydra`). Separate package, post-v0.2.
- **MLflow exporter** for the Store. Separate package, post-v0.2.
- **wandb adapter** showing how to use both in a worker. Documentation only, no separate code.
- **Optuna / search-based sweep generators.** v0.3+.
- **Parallel dispatch.** v0.2.
- **xxhash optional dep.** v0.2.
- **`SubmititLauncher`, `RayLauncher`.** Separate packages, post-v0.2.
- **Web UI.** Never ships from this library — explicitly out of scope; users export to wandb/MLflow for visualization.

## Open questions (to resolve during implementation)

1. **`recv` blocking semantics in FileChannel.** Should `recv(timeout=N)` use `inotify` (Linux-only) for genuine push, or busy-poll with sleep? The Protocol allows both; implementation choice.
2. **`Run.config` serialization in FileStore.** JSON works for most configs but loses dict key order. Use `canonical_json` or `json.dumps(sort_keys=True)`? Pick one.
3. **`flock()` portability for FileStore.** `portalocker` is cross-platform; `fcntl.flock` is Unix-only. Adding `portalocker` as a dep is the price of zero-flakiness on macOS. (Linux + macOS are the realistic v0.1 platforms; Windows can wait.)

## Dependencies

**Runtime:**
- Python 3.11+ (stdlib only for v0.1)
- `portalocker` if cross-platform file locking is needed (small dep; alternative: Unix-only via `fcntl`)

**Dev:**
- `pytest` for tests

That's it. No Hydra, no submitit, no wandb, no MLflow, no Postgres in v0.1.

## Success criteria

v0.1 is shippable when:
1. All five Protocol modules implemented with at least one (or two for Channel/Store) backends
2. Conformance test suite passes for all backends
3. Toy end-to-end test passes: a script using the orchestrator dispatches a 4-run sweep (2 configs × 2 seeds), reuses on second invocation, channel transports messages bidirectionally, store records all four runs in one experiment
4. README documents the elevator pitch (positioning), the worker-side API, the orchestrator-side API, and the slot-in-with-wandb story
5. The library can be `pip install -e ~/src/runstate` and imported as `import runstate` from anywhere

## Repository layout

```
~/src/runstate/
  pyproject.toml
  README.md
  LICENSE                          # MIT
  .gitignore
  runstate/
    __init__.py
    types.py
    channel.py
    store.py
    fingerprint.py
    launcher.py
    orchestrate.py
  tests/
    conftest.py
    test_channel.py
    test_store.py
    test_fingerprint.py
    test_launcher.py
    test_orchestrate.py
  docs/
    design-v0.1.md                 # this file
    README-positioning.md          # the wandb-complementary story for downstream docs
  examples/
    minimal_sweep.py               # demonstration script
```
