# Channel locators: a place, and its attach / create

How a party opens a run's channel. A **place** is one object that says where runs'
logs are; it opens a run two ways, so bringing a run into existence is always an
explicitly-named act and observing one never mutates it. The worker's ambient
factory, `current_channel()`, reads the place back from its environment.

Resolves `docs/backlog/third-party-observer.md` §4 ("There is no read-only open")
and the sharper half recorded in PR #14 (the open is *mutative* on a foreign
file). The demanding consumer is the runstate-tui cockpit's glob resolver (opens
whatever a glob/pointer matches) and its stop button (writes into a resolved run).
The place as an object is #73's (2026-11-09), from GeoffChurch/mycooc's storage
tier: until then the two opens were module functions taking `(root, backend)`
strings, and the split below is unchanged by it.

## The problem

`open_channel` **creates** on open. Two harms, both verified:

- **Phantom.** Opening a missing run fabricates `<rid>.db` (+ `-wal`/`-shm`), so a
  stale/GC'd pointer manufactures an empty run and pollutes a content-addressed
  store — and the API cannot tell "no run" from "empty run" without stepping
  outside it to `os.path.exists`.
- **Foreign-db mutation (the sharp one).** `SqliteChannel.__init__` runs
  `executescript(_SCHEMA)` unconditionally, so a pointer that resolves to a
  *foreign valid sqlite db* (someone else's `.db`) is silently schema-mutated,
  then read at `last_seq()==0` and misrendered as an empty run. `stat-before-open`
  **cannot** catch this — the file exists; the mutation is *at open*.

## The reframe: a run exists iff it has records

The substrate already has a canonical existence semantic, and **PostgresChannel
embodies it**: a run *is* its rows (`WHERE run_id=X`); a nonexistent run and an
empty run are identically zero rows, because in an append-only log a run has no
existence independent of its records. MemoryChannel is the same modulo a
`setdefault` phantom. **SqliteChannel is the anomaly** — `connect()` births the
file and `executescript` writes the schema, giving a run an existence its records
didn't earn; that file-at-open is what manufactures the phantom and mutates the
foreign db.

So the fix is not a new opinion. It is **sqlite and memory conforming to the rule
postgres already follows: a run exists iff `last_seq() > 0`.**

## The design: a place, split on creation policy

**A place is a Strategy** (`runstate.logs`): `SqliteLogs(root)`, `PostgresLogs(dsn)`,
`MemoryLogs()`, or a consumer's own. It is built once, where the program reads its
configuration, and passed down; nothing below asks which backend it holds. Opening
splits on one axis — **creation policy** — into its two methods, each with a
single contract (the `Logs` Protocol):

- **`logs.attach(run_id) -> Channel`** — attach to an existing run. Raises
  **`RunNotFound`** if the run has no records. Never creates or mutates any
  backing store. **Writable** (an attached handle reads *and* writes — the stop
  path attaches then writes).
- **`logs.create(run_id) -> Channel`** — open-or-create (birth); idempotent via
  the birth-CAS. The one side-effecting open.

**`RunNotFound(LookupError)`** is the uniform absence signal across backends.

**A place another process can open says so in its type** (`ProcessLogs`): it adds
`child_env() -> Mapping[str, str]`, its own spelling as environment variables.
`SqliteLogs` and `PostgresLogs` have it; `MemoryLogs` does not, since each
instance holds its logs itself and two handles share a run only by opening it
from the same instance. A launcher that spawns a process takes a `ProcessLogs`
and merges `child_env()` into the child's environment, so a place that cannot
cross the boundary is a type error at the launcher's construction, not an empty
log in the child.

- **`current_channel(json_default=None) -> Channel`** — the worker's own channel:
  `RUNSTATE_RUN_ID`, and the place read back from `RUNSTATE_CHANNEL_BACKEND` and
  `RUNSTATE_CHANNEL_ROOT` (`logs.from_env`, the inverse of the two reference
  `child_env`s), then its `create` (open-or-create, so a direct run that sets the
  three itself still births). All three are required: there is no default place.
  This decode is the one point where a backend is a string.

### A reader

*(#74, 2026-11-09.)* The access axis is orthogonal to creation policy, so it is a
third open and not a flag on `attach`:

- **`logs.reader(run_id) -> Reader`** — open an existing run's log to read.
  `RunNotFound` as `attach`; creates and changes nothing; the handle has no
  `send`.

`Reader` is the read half of the channel surface (`read`, `latest`, `last_seq`,
`close`), and every `Channel` is one. The folds take a `Reader`, so "observing a
run never mutates it" is held by the type checker for every observer, whichever
open it used. The handle is read-only **by type**: nothing wraps it to raise at
run time, since a wrapper would be a second, weaker statement of what the type
already says.

What only a place can do is open more cheaply. `SqliteLogs.reader` opens a
**sealed** file (`seal_sqlite`: checkpointed, out of WAL, write bits cleared) with
no sidecar beside it `mode=ro&immutable=1`: no lock is taken and no journal is
looked for, which a file nobody will write again allows and a network filesystem
with unreliable locks needs. Any other file it opens as `attach` does, because
only a read-write open rolls back a hot journal a dead writer left, so a log
still written cannot be opened read-only at the SQLite level. The place detects
the seal itself; no caller asserts it. A snapshot is replaced by a rename, so a
reader already open keeps the file it opened. `PostgresLogs` and `MemoryLogs`
return their channel under the narrow type.

A consumer's own place (a storage tier that decides per run) implements `create`,
`attach` and a `child_env` returning whatever its own worker reads; that worker
does not call `current_channel()`.

`json_default` is the place's, given at its construction and handed to every
channel it opens.

### Why not the alternatives

- **A flag `open_channel(create=False)`** — a boolean that forks the *exception
  contract* (attach can raise; create can't) and the *side-effect profile*. Two
  total functions, each with one contract, is the canonical form.
- **Lazy file creation** — merely *reschedules* the mutation to the first write;
  the stop path (a writer) still corrupts a foreign db, because only the caller
  can decide to abort the write. And it entrenches the no-run/empty-run ambiguity
  (both read as empty) instead of resolving it.
- **`stat-before-open`** — refuted by PR #14: the foreign-db mutation is atomic
  with open; a pre-check can't catch it.
- **`read_channel`** — the safe path *writes* (stop). The axis is create-vs-attach,
  not read-vs-write. Read-only is a separate axis, and a third open: "A reader",
  below.
- **Module functions taking `(root, backend)`** — what shipped until #73. The
  backend was a string re-dispatched at every open, `root` meant a directory, a
  namespace or a DSN depending on it, and a caller whose place was none of the
  three (a tier) could only subclass a launcher and override the methods its
  internals happened to call.
- **The caller puts the place in `Command.env`** — then a forgotten entry
  type-checks and fails in the child.
- **`child_env` on every place** — `MemoryLogs` has no honest answer, and only a
  process launcher asks. The capability is required exactly where it is used.

## Per-backend mechanism (verified)

A place's `attach`:

- **sqlite**: `sqlite3.connect(f"file:{pathname2url(str(path))}?mode=rw", uri=True)`
  — the path is percent-encoded (`urllib.request.pathname2url`) so a `?`/`#`/`%` in
  a root or run_id is a literal filename character, not URI syntax, matching the
  create path's literal-path file identity (a reimplementer that interpolates the
  raw path mis-parses any such run and falsely reports `RunNotFound`). Raises
  `OperationalError` on a missing file (never creates); **skip** both
  `PRAGMA journal_mode=` and `executescript` (both mutate); probe
  `SELECT COALESCE(MAX(seq),0) FROM log`. A missing file *or* "no such table: log"
  (foreign db) `OperationalError`, or a `0`, → `RunNotFound`; else the handle. A
  genuine `sqlite3.DatabaseError` (corrupt / non-sqlite) propagates. *Verified:*
  missing raises and creates no file; a real WAL run is writable through this
  handle without setting the pragma (the persisted header is inherited); a foreign
  valid db is byte-identical after attach+probe (no `-wal` created).
- **memory**: a lookup in the instance's own logs **without** `setdefault`; run
  absent or log empty → `RunNotFound`.
- **postgres**: probe `MAX(seq) WHERE run_id=X` (the constructor is already
  non-creating — it only checks the *shared table* exists); null/0 → `RunNotFound`.

A place's `create` is the old `open_channel` behavior exactly (sqlite `mode=rwc` +
`journal_mode` + `executescript`; memory `setdefault`; postgres constructor).
There is no default: creation is always the explicitly-named call.

## Addresses and formats

*(Added 2026-10-03; the design is [`log-formats.md`](log-formats.md) §4 — not restated here.)* A log's
format is part of its address. SQLite logs live at `<root>/v<format>/<rid>.db` (today `v0.3.0`; the root
must already exist, the `v<format>/` directory is made on birth); on Postgres each format is a schema
(`runstate_v0_3_0`). Both opens run five checks, in order, before touching the log:

1. a **newer** format present under the root / in the database → `LogFormatMismatch` (upgrade runstate);
2. the current format's address exists → open it;
3. an **older** format's address holds the run → `LogFormatMismatch` (run `runstate migrate`);
4. the **legacy** unversioned `<root>/<rid>.db` holds the run → `LogFormatMissing`, whose message carries
   the one-time onboarding instructions;
5. nothing anywhere → `RunNotFound` (`attach`) or a birth at the current address (`create`).

Both errors subclass `LogFormatError` and are never turned into "not found". `MemoryLogs` has no
address and skips the checks.

## Classification principle (for the call-site audit)

The split matters at the boundary where a party opens a run **it does not own**.
Own-your-run components create; third-party surfaces attach:

- **`create`** (birth): the worker's own channel (`current_channel`), the
  launcher writing `supervisor.launched`, the memoizer's producer extending a run,
  birthing tests.
- **`attach`** (existing-only): observers, `Watcher`, `observables`, the
  stop path, and the launcher **deciders' probe handles** (`relaunch_if_needed` /
  `ensure_served`) — where `RunNotFound` *is* the "not launched yet → launch it"
  signal, caught, not a phantom.

A launcher holds its place and exposes it (`Launcher.logs`): the launch flow calls
`logs.create`, the deciders `launcher.logs.attach`, catching `RunNotFound`. The
place is the launcher's, so a decider and the launch it decides cannot be pointed
at two places.

## Rubric verdict

- **Independence** — PR #14 proved the foreign-db half is not derivable from any
  pre-check; `attach` is the only place the non-mutating open can live. `child_env`
  is not derivable either: only the place knows what opens it.
- **Orthogonality / Serendipity** — keeping the split on creation-policy
  (orthogonal to access mode) lets `attach` serve both the observer's read
  and the controller's write; it retires the `stat-before-open` workaround and
  dissolves the no-run/empty-run ambiguity (`RunNotFound` is the answer).
- **Canonical form** — two total methods over the file/`O_CREAT` split, on the
  one object that names where; the backend is resolved once, at construction; "a run
  exists iff it has records" is the predicate postgres already embodies.
  `attach`/`create` are clean antonyms; `RunNotFound(LookupError)` is the canonical
  lookup-miss.
- **Opinion-free** — no workload vocabulary; the substrate becomes *more* uniform
  (sqlite/memory conform to postgres).

## Migration

`open_channel` is **removed** (no alias — migrate, don't accommodate), and so, since
#73, are the module functions `attach_channel` / `create_channel` and the
launchers' `root=` / `backend=`. A consumer builds its place where it reads its
configuration (`SqliteLogs(root)`), passes it to its launchers (`logs=`), and
opens through it; every call site is still classified create-vs-attach by the
principle above, and an un-migrated birth site fails **loudly** (`RunNotFound` at
first run), never silently. A direct run that relied on the default backend sets
`RUNSTATE_CHANNEL_BACKEND` itself. Consumer repos migrate under separate owner
authorization, when they bump their pin.

## Test pins

- `attach` raises `RunNotFound` on a missing run **and** on an empty run
  (every backend).
- `attach` leaves a foreign valid sqlite db **byte-identical** (the PR #14
  harm, pinned).
- `attach` is **writable**: a `control.stop` into an existing run
  round-trips.
- `create` reproduces the old create-on-open behavior (the conformance
  suite passes unchanged under it).
- `current_channel()` reads the place's own `child_env()` and births; with any of
  its three variables unset it raises `KeyError`.
- `from_env` inverts both reference `child_env`s and refuses `memory`.
- Two `MemoryLogs` instances are two places; one instance shares a run's log.
- `reader` raises `RunNotFound` on a missing and on an empty run, and reads what
  `attach` reads (every backend).
- A sealed snapshot's reader has no `send`, changes no byte and creates no
  sidecar, reads while another connection holds the file locked exclusively, and
  outlives the snapshot's replacement by rename.
- An unsealed file, and a sealed one beside a `-wal` or `-journal`, are read
  through a channel.
