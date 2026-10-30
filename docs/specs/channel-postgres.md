# `PostgresChannel` — a cross-host substrate backend

**Status:** SHIPPED — `runstate/channel/postgres.py` (tests: `tests/test_postgres_channel.py`
+ the backend-parametrized conformance/concurrency suites; CI runs them against a Postgres
service). Shipped core: the four ops + the CAS over one shared `log` table, and the advisory
lock as a Watcher-consumed liveness signal (the `EpisodeHolder`/`EpisodeProbe` capability,
resolved at the Watcher's boundary into a per-run probe). Still deferred: low-latency push,
cross-host auto-relaunch, sharding, HA (see *Deferred / rejected-for-now*). Motivating use
case: a controller on *another
host* (a viz dashboard, a Bayesian-optimizer) that both **reads** run state and **writes
cooperative control** (`control.stop` / `control.subscribe`) to runs whose workers are
elsewhere — the control plane is the differentiator vs read-only trackers (a dashboard
stop button; a BO that preempts a losing trial cooperatively → resumable `preempted`).

Hardened by three four-lens adversarial review rounds before implementation; the findings
are folded in.

## The one design principle (load-bearing)

Two concerns are kept **orthogonal**, and this is what makes the backend clean:

- **Claim = one uniform substrate primitive: the CAS** (`send(expected_seq=)`). Backends
  differ *only* in how they implement it. The claim model is identical across backends;
  nothing above the substrate is backend-aware about claiming.
- **Liveness = a poset of signals, combined in one clock-aware place: the Watcher.**
  Backends contribute *better* liveness signals as **opt-in capabilities the Watcher
  consumes** — never as claim arbiters.

So Postgres-on-a-shared-server makes the CAS cross-host-reliable (cross-host single-spawn
and cross-host `control.stop` **fall out for free, claim model unchanged**), and the
advisory lock is a *liveness signal*, not a claim gate. The stateless observables
(`live_episode`) stay conservative and backend-agnostic. **Pushing liveness into the claim
path is the one thing that breaks this layering** — see *Deferred* (the co-arbiter).

## Why Postgres (vs sqlite / file / rsync)

One shared server: worker (host A), dashboard (host B), BO (host C) all on one log.

- **One total order.** The control folds join on names (`reference-by-name.md`), but the
  claims are ordered by the one sequencer: a claim's name is the log's `seq`, well-defined
  only on a single shared log — so cross-host claims and `control.stop` just work.
  (Replication/rsync = many homes → the deferred causal regime.)
- **A reliable cross-host CAS.** The birth-CAS on the shared log serializes claims across
  hosts → cross-host single-spawn, which NFS-sqlite can't do.

## Schema — one shared `log` table

```sql
CREATE TABLE IF NOT EXISTS log (
    run_id        text   NOT NULL,
    seq           bigint NOT NULL,
    topic         text   NOT NULL,
    name          text,
    request_id    text,
    body          text   NOT NULL,       -- opaque JSON; see "body stays text"
    created_at    double precision NOT NULL,
    writer_key    bigint,                -- the session stamp; see "Liveness"
    writer_server text,
    PRIMARY KEY (run_id, seq)            -- per-run contiguous seq + the arbiter
);
CREATE INDEX IF NOT EXISTS idx_log_run_topic_seq ON log (run_id, topic, seq);
```

The table is the format's, not the channel's: its module (`runstate/formats/v0_3_0.py` and its
siblings) states it (`pg_ddl()`), and every writer, `ensure_schema` or a migration's destination,
runs the DDL of the format it writes ([`log-formats.md`](log-formats.md) §3). The stamp columns
are the session probe's.

The conformance suite's **contiguous-seq** tests force `seq = MAX(seq)+1` per run (a global
`SERIAL` gaps on rollback and isn't per-run). The `PRIMARY KEY (run_id, seq)` is then the
concurrency arbiter — SQLite's atomic-by-construction guarded INSERT, in PK form. One
shared table (not table-per-run) so the viz/BO side can query across runs with no DDL per
run.

- **`body` stays `text` — the *only* correct choice, not a simplification.** `jsonb` would
  reorder keys, drop duplicates, canonicalize numbers/whitespace — *mutating the opaque
  body*, breaking the byte-fidelity / immutable-snapshot contract.
- **`created_at`** is `extract(epoch from clock_timestamp())` — a server-side wall-clock
  `double precision` (matching the column type; the client's `time.time()` would be
  cross-host-skewed). Order is `seq`; `created_at` is advisory.
- **Prefix wildcard:** `topic = "control.>"` → `topic LIKE 'control.%'` with the prefix's
  LIKE-metacharacters (`%`, `_`) escaped (arbitrary user topics), and a `text_pattern_ops`
  (or C-collation) index so the prefix scan is a range seek on the per-poll path.

### Schema ownership — out of the hot path

`CREATE TABLE IF NOT EXISTS` is **not concurrency-safe** in Postgres (concurrent
first-connectors race on the `pg_type`/`pg_class` catalog — the analogue of SQLite's
WAL-birth race). So DDL is **not** in `__init__`. A one-time `ensure_schema(dsn)` creates the
table, **wrapping the DDL in a server-side `pg_advisory_xact_lock`** (`BEGIN; SELECT
pg_advisory_xact_lock(K); CREATE TABLE IF NOT EXISTS …; CREATE INDEX IF NOT EXISTS …; COMMIT`,
where `K` is the interop constant `0x72756E7374617465` — the 8 ASCII bytes of `"runstate"` as
one int8)
— concurrency-safe **cross-host**, so two orchestrators cold-starting against a fresh DB can't
race the `pg_type` catalog, and it subsumes any host-local test lock (one mechanism, per the
rubric — a `FileLock` would be a redundant weaker second primitive). `__init__` cheaply probes
`to_regclass('log')` and raises a clear *"run ensure_schema(dsn) first"* on absence (never a raw
`UndefinedTable`); the orchestration helpers (`sweep`, the launchers) call `ensure_schema` at
startup so cold-start-many-workers is self-sufficient.

### The schema is per format

*(Added 2026-10-03; [`log-formats.md`](log-formats.md).)* The shared `log` table above lives in a schema
named for the log format, `runstate_v<format>` with dots as underscores (today `runstate_v0_3_0`).
`ensure_schema` creates that schema and the table inside it, under the same advisory lock. The channel
runs `SET search_path` to the schema at open, so its unqualified SQL (and `to_regclass('log')`) resolves
there. That is a session setting: **a transaction-mode pooler drops it**, the same reason the advisory
lock needs a direct or session-pooled endpoint (see *Liveness* below). Opening also runs the
format checks of [`channel-locators.md`](channel-locators.md#addresses-and-formats) across the schemas.

## The four operations (the conformant core)

**Connection & transaction model (load-bearing).** Each `PostgresChannel` holds **one
dedicated persistent connection** in **`autocommit=True`**, serialized by an **internal
`threading.Lock`** (psycopg connections aren't safe for concurrent statement use — the
shared-handle topology, `test_shared_handle_concurrent_sends_are_serialized`, needs this,
exactly as `SqliteChannel._lock` does). `__init__` issues `SET lock_timeout = '5000ms'`
(the `busy_timeout=5000` analogue). `autocommit` is doubly load-bearing: (i) else a CAS
`INSERT` stays uncommitted → invisible to other writers' `MAX(seq)` and holding the
unique-index lock → the run stalls; (ii) in autocommit a failed statement leaves the
connection idle (not in an aborted transaction), so a `UniqueViolation` raises cleanly with
nothing to roll back. The endpoint must be **direct or session-pooled** — a transaction-mode
pooler (pgbouncer) reassigns the backend per statement, silently breaking the session lock
(and, in v2, LISTEN); this is both documented *and* self-checked (see *Liveness*).

### `send` with `expected_seq` — the CAS
```sql
INSERT INTO log (run_id, seq, topic, name, request_id, body, created_at)
SELECT %(run)s, %(expected)s + 1, %(topic)s, %(name)s, %(rid)s, %(body)s,
       extract(epoch from clock_timestamp())
WHERE (SELECT COALESCE(MAX(seq), 0) FROM log WHERE run_id = %(run)s) = %(expected)s
```
- rowcount 1 → return `expected+1`.
- rowcount 0 (gate false, log moved) **or** `UniqueViolation` (a rival committed the same
  `seq`) → `None` (provably lost). The catch is **`UniqueViolation`-specific** — a blanket
  `except psycopg.Error` would map a lock/connection fault to a synthesized loss.
- connection drop / `lock_timeout` exhaustion → **raise** (indeterminate — never synthesize
  a loss).

**Postgres needs no `SQLITE_BUSY` re-check** (a real simplification). Row/index-level
locking means the guarded INSERT blocks only on an *uncommitted* conflicting `(run,
expected+1)` — one in-flight rival: `lock_timeout ⟺ raise`; the rival's `commit ⟺
UniqueViolation → None`; its `rollback ⟹ proceed`. There is no "timeout but the log already
moved" case (a SQLite artifact of its single database-wide write lock) — so the post-busy
re-check is dropped. (This holds *given* `lock_timeout` is set — without it the wedged-writer
case hangs instead of raising.)

### `send` unconditional
`MAX(seq)+1` + retry on `UniqueViolation` (the PK is the sole arbiter; optimistic, no second
locking primitive). Terminates (each round ≥1 commits; `MAX` strictly advances). **Bounded
with jitter** — per-run write contention is low by construction (the worker is the sole
value/lifecycle writer; cross-host `control.*` from a dashboard/BO is a legitimate second writer
the retry absorbs), but a substrate primitive states its bound; bound-exhaustion **raises**
(a fault), never returns None.

### `read` / `latest`
The existing filters (`after`, escaped `topics` incl. `prefix.>`, `name`, `request_ids` +
unaddressed broadcasts, `limit`) + `WHERE run_id = %(run)s`; `latest` rides
`idx_log_run_topic_seq`.

### `close`
Release this handle's connection (which drops any session advisory lock it holds).

## Liveness — the session probe

*(Rewritten 2026-10-30 for log format 0.4.0: [`commits-and-lineage.md`](commits-and-lineage.md) §3
and §11, review §12, item 15, ruling 12b. It replaces the episode-keyed lock taken after the claim
CAS, and its birth grace.)*

**The principle, restated.** The probe yields **evidence for a record**, which `report_if_dead`
writes as a death report (`lost`, with `session:` and the session as its evidence); the CAS stays
the **only arbiter of a claim**. The probe never gates a claim and never decides a verdict by
itself: it says that the session that wrote a claim has ended, and a party that reads that writes
it down.

**A key per connection, before its first write.** Every `PostgresChannel` connection, at its open
and before any write, draws a **fresh random 64-bit key** and takes it with
`pg_try_advisory_lock`, drawing again while another connection holds it (at most 16 draws, then a
raise). The key is never derived from the run or the claim: every racing claimant shares those, so
a derived key would turn the lock into a claim gate. Session-scoped, so the lock is released when
the connection ends, by `close`, a crash or a server-side termination; and since an ending session
aborts its unfinished write before it releases its locks, no row of that session becomes visible
after the lock reads released (294 trials over kills, restarts, a lost commit acknowledgement and
`idle_session_timeout`; review §12, item 15). Taken before the first write, so there is no window
in which a claim's row exists and its session holds nothing.

**The stamp.** The backend stamps every row it writes with its writer's key and the server's
identity, in two nullable columns of the format's table (`writer_key bigint`, `writer_server
text`), both written by the CAS and by the unconditional append. No wire field carries it: it is
the backend's, carried losslessly beside the record. A row the migration store copies carries no
stamp, and the probe abstains on it.

**The server's identity** is its **system identifier** (`pg_control_system()`), which a physical
standby shares, and the **timeline its WAL is being inserted on**,
`(pg_split_walfile_name(pg_walfile_name(pg_current_wal_lsn()))).timeline_id`, which a promotion
advances at once. A non-superuser can read both. The identity is computed **per row**, inside the
statement that writes it, so a stamp is always the timeline the row was written on, even on a
connection opened on a standby that has since been promoted (an INSERT succeeds only out of
recovery, where `pg_current_wal_lsn()` is defined). Only the key is per connection.
- **Not the checkpoint's timeline** (`pg_control_checkpoint()`). Since PostgreSQL 13 a promotion
  writes only an end-of-recovery record and requests a *spread* checkpoint, so the checkpoint's
  timeline stays the old one long after it. Reproduced on PostgreSQL 18.4 with a streaming standby
  and `pg_promote(true)`: for more than 100 s after the promotion, a channel on the promoted server
  reported the old identity, and `session_ended` returned `True` for a writer still alive on the old
  primary. A connection opened in that window would also have stamped the old timeline for its
  whole life, had the identity been read once at the open.
- **PostgreSQL 16 is runstate's floor**, because `pg_split_walfile_name` arrived in 16. CI runs
  `postgres:16`, and every known deployment is 16 or newer.
- **Revival trigger:** a consumer on PostgreSQL 15 or older. Then add a parse of the WAL file name's
  timeline (its first 8 hex digits) as a Strategy resolved at the open. The stamp's value is the
  same integer either way, so no log changes.

**The probe.** `SessionProbe` (`channel/base.py`) is an opt-in capability, isinstance-dispatched;
`PostgresChannel` satisfies it and the other backends do not:
```python
class Session(NamedTuple):
    server: str   # the writing server's identity
    key: int      # the writer connection's advisory-lock key, a signed 64-bit integer

@runtime_checkable
class SessionProbe(Protocol):
    def writer_session(self, seq: int) -> Session | None: ...      # None: an unstamped row
    def session_ended(self, session: Session) -> bool | None: ...  # None: cannot tell here
```
`session_ended` reads `pg_locks`, read-only, for the key in **this database** (`pg_locks` is
cluster-wide, and advisory keys are per database). It reads **only the server that wrote the
stamp, never a standby**: it reads the server's current identity at each probe, as `CASE WHEN
pg_is_in_recovery() THEN NULL ELSE <identity> END`, and abstains (`None`) in recovery or on a
mismatch, since a standby's `pg_locks` know nothing of the primary's sessions.

**Every record naming a claim travels on the claim's session, never re-established.** A channel
never reconnects: a dropped connection makes its next `send` raise, and the launcher relaunches
(a fresh claim on a fresh session). So once the claim's session has ended, nothing can commit a
record in its name; a value sent through another handle can land after the report, but it names
no claim. The probe's evidence is about the session, not the process, which may outlive its
session until its next write raises: so it outranks a live pid on the same host.

**A durable server.** The channel sets **`synchronous_commit = on`** on its own connection,
whatever the database or role defaults to, and **refuses to open on a server with `fsync = off`**
(`RuntimeError`). Under either, a crash loses rows that readers already saw and the log reuses
their seqs: a crash experiment with `fsync = off` lost 14 to 2,386 already-visible rows in 40 of
40 trials (review §12, item 15). A lost row a reader saw breaks every cursor, and a reused seq
makes one name mean two records.

**No transaction-mode pooler.** The key, the search path and the stamp are session state. A
transaction-mode pooler (pgbouncer) reassigns the backend per statement, so the lock would be held
by a session that is not the writer's. **Pooler self-check:** at the open, right after taking the
key, the connection confirms in `pg_locks` that its own backend holds it, and raises *"a
transaction-mode pooler is in the path"* otherwise. It runs before any write, so a pooler fails
the open and can never strand a claim with no exit.

**Partition / idle-reap.** A clean death releases the lock at once; a hard network partition
releases it only when the server's TCP keepalive fires, so set server-side
`tcp_keepalives_idle/interval/count` in seconds, not the 2-hour default (release time under a true
partition is not measured). An `idle_session_timeout` or a NAT idle-drop on a worker endpoint ends
a healthy worker's session: the probe then reads it ended, which is true, since that worker can
write nothing more; its next `send` raises and it is relaunched. Keep the heartbeat cadence below
any idle-reap timeout, and set client-side libpq keepalives.

**What it carries, and does not.** The probe carries no cause: a walltime kill seen only by it
reads `lost`, which is a failure, where accounting would say `preempted`. A deployment that wants
the cause keeps an accounting reporter beside it (`commits-and-lineage.md` §12).

## Locator + dependency

`create_channel` / `attach_channel` `(run_id, root=<DSN>, backend="postgres")` — `root` is the
connection string (or `RUNSTATE_PG_DSN`), a **direct or session-pooled** endpoint. Dependency:
`psycopg` v3 as an optional `runstate[postgres] = ["psycopg[binary]"]` extra; the locators'
`"postgres"` branch imports it lazily and raises a clear *"pip install runstate[postgres]"* on
absence.
The server must be **PostgreSQL 16 or newer** (see *Liveness*, the server's identity). The
`SessionProbe` Protocol (with `Session`), the `EpisodeHolder` / `EpisodeProbe` Protocols and the
`isinstance`-dispatch sites live in a **psycopg-free module** (`channel/base.py`, `watcher`), so `import runstate` works without
the extra (guarded by a CI job that imports without it).

## Convention-bump migrations on the shared table

The develop-by-migration doctrine (offline scripts, committed → run to
convergence → deleted; the lifecycle-v0.3 and observer-clock-v0.4 precedents) has
only ever had a sqlite corpus to run against — no Postgres corpus exists outside
CI as of 2026-07-16. When one appears, the observer-clock (v0.4) pass has a known
head start, preserved here from the deleted sqlite script's docstring (PR #2)
because it otherwise exists nowhere in the tree:

```sql
UPDATE log SET body = jsonb_set(body::jsonb, '{t}', to_jsonb(created_at))::text
 WHERE topic IN ('lifecycle.heartbeat','lifecycle.stopped',
                 'launcher.launched','launcher.terminated')
   AND NOT (body::jsonb ? 't') AND run_id = %s;   -- plus the started rename
```

…run per **quiescent** run (`live_episode(ch) is None`), the same gate-then-stamp
shape as the sqlite walk. It was deliberately not shipped as a `--dsn` variant of
that script: the per-run quiescence gate + the shared-table scoping don't stay a
one-liner — every run on the one `log` table needs its own gate-then-UPDATE,
where the sqlite pass gets per-run isolation for free from one-file-per-run.
(A migration deliberately rewrites the stored body, so the `body`-stays-`text`
rule — never mutate a row on a read path — is not in tension with the
`::jsonb` round-trip here.)

## Deferred / rejected-for-now

- **Cross-host auto-relaunch — the "co-arbiter" — rejected for v1 (and as the default
  design).** Making the lock a *claim* gate (taken before the CAS so a dead foreign run is
  re-claimable) is the only way to give a self-claiming worker clock-aware cross-host
  liveness — but it **conflates claim with liveness and is cross-cutting**: it changes
  `worker.py`'s claim logic + the stop/`retire` path, `observables.live_episode`, the
  launchers, and the run-episodes claim invariant ("the CAS is the guarantee"); it forces
  run-keyed locks with explicit release-on-stop; and it isn't needed for the motivating
  use case. If a real cross-host auto-relaunch need appears, it's a deliberate claim-model
  redesign (lock-before-CAS co-arbiter, or a Watcher-driven force-claim), specced and
  reviewed on its own — not folded into this backend.
- **Low-latency push (LISTEN/NOTIFY).** A `@runtime_checkable Waitable` (`wait(timeout)`;
  Postgres LISTEN on a *dedicated* connection) for the **single-run** waiters
  (`ensure`/`history`). The multi-run dashboard case needs a *multiplexed* listener — really
  the separate viewer project. `NOTIFY` must be best-effort (never fail the append; bounded
  hash channel name, given the 63-byte identifier limit; polling is the floor).
- **Sharding** (`run_id → DSN`; per-run independence makes the surface shard-ready) with
  cross-run discovery from a derived, never-authoritative index (`../specs/store.md` Recipe
  5), not by co-locating all writes.
- **HA.** Single-instance design; streaming replication re-admits a `seq` on failover → the
  causal/discharge-by-id regime, a different substrate — not a config flag.
- **Table partitioning** by `run_id` for per-partition VACUUM/retention/GC (in-log GC is
  §12.9, still open).

## Galaxy-scale note

Contiguous per-run seq is inherently a **single-serialization-point** construct (one server
= one total order); the unique-constraint arbiter is right for it. True galaxy-scale
(spacelike writers, no global order) is the deferred **causal** regime. The horizontal lever
within this design is **shard-by-run** (deferred), not pushing the seq further. Named
single-instance ceilings: dedicated connections cap fleet concurrency at ~`max_connections`
(the session lock blocks a transaction-pooler escape), a dashboard/BO watching N runs holds
N connections (observer amplification — a multiplexed *read* connection is the v2 mitigation),
and the instance is a fleet-wide SPOF — the honest cost of one shared total order.

## Tests

**Isolation is real fixture work, not "almost all tests already exist."** A shared `log`
table has none of `tmp_path`'s per-test freshness. The Postgres `ch` / `open_run`
fixtures mint a **unique uuid `run_id` per test** (transparent to test *bodies*, which don't
pass run_ids); the two `conc_backend` concurrency tests that build their own run_ids
(`race-{trial}`, `claim-{trial}`) take a per-test uuid **namespace** (test-body edits). With
unique run_ids the per-run `MAX+1` keeps seqs 1-based, so content-pinning conformance holds.
**No per-test `TRUNCATE`** (it takes ACCESS EXCLUSIVE and would nuke other xdist workers'
in-flight rows — uuid isolation suffices); one session cleanup. No psycopg connection is
held across a `fork`ing test (inherited-fd corruption); every fixture/path guarantees
`close()`; a session-end check asserts the test role's connection count returns to baseline
(catch leaks → `max_connections`).

**Cross-host honesty.** With one CI Postgres the `cross_host` tier is **multi-client to one
server, not multi-host** — named as such. The genuinely cross-host property is tested *on one
host*: seed a `lifecycle.started` whose handle is `local://OTHER-HOST/<pid>` (which `resolve`
abstains on → `None`), hold the lock from a separate connection, assert `episode_alive` is
**True** (the lock answers where `resolve` cannot), then release → **False**.

**The rest:** the core conformance + `cross_process` tiers (parametrize the fixtures);
postgres-specific — shared-table isolation (two interleaved runs keep independent 1,2,3
seqs); an **unconditional-send contention** test (N connections × M plain `send`s on one run →
seqs exactly `1..N·M`, no loss/dup — the cross-host `control.*` write path); **Watcher grace/
floor** tests (clock-injected: held→alive→still-staleness; not-held-past-grace→`presumed_dead`;
not-held-within-grace→fall-through; held-but-stale→`presumed_dead`) and a Watcher-level
`observe()`-d-run→`presumed_dead`-via-the-lock test (the handle-free motivating path); the CAS
`UniqueViolation→None` + a *cross-host wedged-writer → raise* (a second
connection holds an uncommitted `(run, expected+1)`; depends on `lock_timeout`); the
internal-send-lock under the shared-handle topology; and liveness — a **`hold_episode` ↔
`episode_alive` same-process agreement** test (real holder writes, independent observer reads
`True` — catches a writer/observer key mismatch the seeded-lock oracle can't), held→alive
(synchronous), and clean-`close()` / subprocess-`SIGKILL` → dead via a **bounded
poll-until-converged** (the SIGKILL test first polls until `granted` to avoid a
kill-before-acquire false pass). Infra: skip if `RUNSTATE_TEST_PG_DSN` unset; a **new** CI
workflow (the first one — it must also run the existing non-PG suite) adds a Postgres
*service* + a **direct** DSN, pinned **serial** (the shared-table fixtures are xdist-unsafe),
plus the import-without-psycopg job. All `mypy --strict` + `py.typed` clean.

## Orthonormal-basis check

- **CAS / seq** — no new primitive: the `PRIMARY KEY (run_id, seq)` *is* the contiguous-seq
  invariant, reused as the sole arbiter for the CAS and the unconditional append. Canonical,
  and sharper than SQLite (no busy re-check).
- **the liveness lock** — *not* a claim primitive and *not* a new tier: a **new probe signal
  in the Watcher's liveness combination**, where `os.kill` abstains. Independence holds
  (definitive cross-host liveness isn't derivable from the four ops — the log gives only
  heartbeat *inference*); canonical (a connection-bound session lock *is* connection-liveness);
  it's consumed *only* in the Watcher (clock-aware: grace + staleness floor), never in the
  claim path — which is what keeps claim and liveness orthogonal.
- **`EpisodeHolder` / `EpisodeProbe`** — optional capability Protocols off the base ABC (the
  `collections.abc` base-plus-mixins shape), split by viewpoint so the type can't steer an
  observer to `hold_episode`. The substrate ABC stays the four pure-data ops.
- **type-safety** — `isinstance` on a `@runtime_checkable` Protocol is a *structural*
  (method-name) check that **mypy narrows** — the codebase's first runtime-structural check
  (existing `isinstance` is on concrete classes). Honest caveats: "zero escapes" is not
  literal — `sqlite.py` has two *defensive* `getattr(exc, "sqlite_errorcode", …)` reads (a
  different kind than capability dispatch); there remain zero `# type: ignore` / `cast` /
  dispatch-duck-typing.
- **opinion-free** — `body` stays opaque `text`; the claim model is untouched and uniform;
  the lock is liveness, never claim; convention knowledge ("take the lock on claim") stays
  in the worker, never the substrate.
