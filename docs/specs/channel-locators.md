# Channel locators: attach / create / current

How a party opens a run's channel. Splits the old creating `open_channel` into
two total locators plus the worker's ambient factory, so bringing a run into
existence is always an explicitly-named act and observing one never mutates it.

Resolves `docs/backlog/third-party-observer.md` §4 ("There is no read-only open")
and the sharper half recorded in PR #14 (the open is *mutative* on a foreign
file). The demanding consumer is the runstate-tui cockpit's glob resolver (opens
whatever a glob/pointer matches) and its stop button (writes into a resolved run).

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

## The design: split the locator; kill the flag

Opening splits on one axis — **creation policy** — into two total functions, each
with a single contract, plus the worker's ambient factory:

- **`attach_channel(run_id, *, root, backend, json_default=None) -> Channel`** —
  attach to an existing run. Raises **`RunNotFound`** if the run has no records.
  Never creates or mutates any backing store. **Writable** (an attached handle
  reads *and* writes — the stop path attaches then writes).
- **`create_channel(run_id, *, root, backend, json_default=None) -> Channel`** —
  open-or-create (birth); idempotent via the birth-CAS. The one side-effecting
  locator.
- **`current_channel(json_default=None) -> Channel`** — the worker's own channel,
  identity read from `RUNSTATE_RUN_ID` / `RUNSTATE_CHANNEL_ROOT` /
  `RUNSTATE_CHANNEL_BACKEND`; delegates to `create_channel` (open-or-create, so a
  launcher-less direct run still births). **Replaces `attach()`.**

**`RunNotFound(LookupError)`** is the uniform absence signal across backends.

Both explicit locators are thin wrappers over a private
`_locate(run_id, *, root, backend, create, json_default)`; the boolean survives
*internally* (implementation) and never appears in the public basis.

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
  not read-vs-write. A pure read-only "touch nothing" mode is a separate, deferred
  axis; no consumer needs it yet.

## Per-backend mechanism (verified)

`attach_channel` = `_locate(create=False)`:

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
- **memory**: registry lookup **without** `setdefault`; key absent or log empty →
  `RunNotFound`.
- **postgres**: probe `MAX(seq) WHERE run_id=X` (the constructor is already
  non-creating — it only checks the *shared table* exists); null/0 → `RunNotFound`.

`create_channel` = `_locate(create=True)` = today's `open_channel` behavior exactly
(sqlite `mode=rwc` + `journal_mode` + `executescript`; memory `setdefault`;
postgres constructor). There is no default: creation is always the explicitly-named
call.

## Classification principle (for the call-site audit)

The split matters at the boundary where a party opens a run **it does not own**.
Own-your-run components create; third-party surfaces attach:

- **`create_channel`** (birth): the worker's own channel (`current_channel`), the
  launcher writing `launcher.launched`, the memoizer's producer extending a run,
  `sweep`'s run setup, birthing tests.
- **`attach_channel`** (existing-only): observers, `Watcher`, `observables`, the
  stop path, and the launcher **deciders' probe handles** (`relaunch_if_needed` /
  `ensure_served`) — where `RunNotFound` *is* the "not launched yet → launch it"
  signal, caught, not a phantom.

`launcher.py`'s dual-use `open_channel` **method** splits accordingly: the Launcher
Protocol offers both `create_channel` (launch flow) and `attach_channel` (probe),
each binding the launcher's `root`/`backend`; the deciders catch `RunNotFound`.

## Rubric verdict

- **Independence** — PR #14 proved the foreign-db half is not derivable from any
  pre-check; `attach_channel` is the only place the non-mutating open can live.
- **Orthogonality / Serendipity** — keeping the split on creation-policy
  (orthogonal to access mode) lets `attach_channel` serve both the observer's read
  and the controller's write; it retires the `stat-before-open` workaround and
  dissolves the no-run/empty-run ambiguity (`RunNotFound` is the answer).
- **Canonical form** — two total functions over the file/`O_CREAT` split; "a run
  exists iff it has records" is the predicate postgres already embodies.
  `attach`/`create` are clean antonyms; `RunNotFound(LookupError)` is the canonical
  lookup-miss.
- **Opinion-free** — no workload vocabulary; the substrate becomes *more* uniform
  (sqlite/memory conform to postgres).

## Migration

`open_channel` is **removed** (no alias — migrate, don't accommodate). `attach()` →
`current_channel()`. Every call site is reclassified create-vs-attach by the
principle above; an un-migrated birth site fails **loudly** (`RunNotFound` at first
run), never silently. Consumer repos (mycooc / translation import
`open_channel` / `attach`) migrate under separate owner authorization — mostly
worker `attach()` → `current_channel()` and observer opens → `attach_channel`.

## Test pins

- `attach_channel` raises `RunNotFound` on a missing run **and** on an empty run
  (every backend).
- `attach_channel` leaves a foreign valid sqlite db **byte-identical** (the PR #14
  harm, pinned).
- `attach_channel` is **writable**: a `control.stop` into an existing run
  round-trips.
- `create_channel` reproduces the old create-on-open behavior (the conformance
  suite passes unchanged under it).
- `current_channel()` reads the `RUNSTATE_*` env and births.
