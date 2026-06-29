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

- **One total order.** The control folds are positional ("pending until the *next*
  `lifecycle.stopped`"), well-defined only on a single shared log — so cross-host
  `control.stop` just works. (Replication/rsync = many homes → the deferred causal regime.)
- **A reliable cross-host CAS.** The birth-CAS on the shared log serializes claims across
  hosts → cross-host single-spawn, which NFS-sqlite can't do.

## Schema — one shared `log` table

```sql
CREATE TABLE IF NOT EXISTS log (
    run_id     text   NOT NULL,
    seq        bigint NOT NULL,
    topic      text   NOT NULL,
    name       text,
    request_id text,
    body       text   NOT NULL,          -- opaque JSON; see "body stays text"
    created_at double precision NOT NULL,
    PRIMARY KEY (run_id, seq)            -- per-run contiguous seq + the arbiter
);
CREATE INDEX IF NOT EXISTS idx_log_run_topic_seq ON log (run_id, topic, seq);
```

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
pg_advisory_xact_lock(K); CREATE TABLE IF NOT EXISTS …; CREATE INDEX IF NOT EXISTS …; COMMIT`)
— concurrency-safe **cross-host**, so two orchestrators cold-starting against a fresh DB can't
race the `pg_type` catalog, and it subsumes any host-local test lock (one mechanism, per the
rubric — a `FileLock` would be a redundant weaker second primitive). `__init__` cheaply probes
`to_regclass('log')` and raises a clear *"run ensure_schema(dsn) first"* on absence (never a raw
`UndefinedTable`); the orchestration helpers (`sweep`, the launchers) call `ensure_schema` at
startup so cold-start-many-workers is self-sufficient.

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

## Liveness — the lock as a Watcher-consumed signal (v1)

**The claim path is untouched.** `Worker.__init__`'s pre-check and `relaunch_if_needed` /
`ensure_served` keep using `live_episode`, which stays **conservative and backend-agnostic**
(`resolve` → `os.kill`; a foreign host abstains → treated as live). Cross-host single-spawn
is delivered by the *CAS* alone (two cross-host workers race the PK; one wins, one gets
`None`). The lock adds **definitive cross-host death detection** in the Watcher — it does
**not** arbitrate the claim, so it cannot produce the double-live that wiring it into the
claim path would (the round-2 finding).

**Mechanism.** After winning the birth-CAS, the worker's channel connection takes a
**session-scoped** `pg_advisory_lock(key(run_id, started_seq))` — *episode*-keyed, taken
*after* the CAS (it's a signal, not a gate). Held for the episode's life by that one
connection; worker death drops the connection → auto-release. An observer reads it
**read-only via `pg_locks`** (`granted` on the key for the latest episode), never
`pg_try_advisory_lock` (which would have observers contend and misread). Two capability
Protocols, split by viewpoint (self-report vs external-report — so a pure observer's channel
never advertises a method it must not call):
```python
@runtime_checkable
class EpisodeHolder(Protocol):                       # worker side, after the claim
    def hold_episode(self, started_seq: int) -> None: ...
@runtime_checkable
class EpisodeProbe(Protocol):                        # observer side
    def episode_alive(self, started_seq: int) -> bool: ...   # is this episode's lock held?
```
The key is a deterministic **int8** — a stable hash of the *full* `(run_id, started_seq)` pair
(e.g. `hashtextextended`), **not** a two-`int4` form (which collapses to a 32-bit collision
domain on `hash(run_id)`, since first episodes share `started_seq` — colliding at ~65k
concurrent runs on the very many-runs-one-server topology this targets) and **not** Python's
salted `hash()` (the observer must recompute a bit-identical key cross-process).

`hold_episode(started_seq)` takes `started_seq` explicitly (the worker has its winning-CAS
seq; the channel does not) and is the **one additive core touch**: `Worker.__init__`, after
winning, calls `self._ch.hold_episode(claim)` iff `isinstance(self._ch, EpisodeHolder)`. No
explicit release is needed — the connection lifecycle (death, or `close`) releases it, and a
cleanly-stopped episode is caught by `peek_terminal`'s record tier (the Watcher's tiers 1-2)
before the lock is ever probed.

**Pooler self-check.** In `hold_episode`, after taking the lock, confirm it's visible in
`pg_locks` for `pg_backend_pid()` on the *same* connection; if not, raise *"transaction-mode
pooler detected; use a direct/session endpoint"* — turning a silent corruption into a loud
startup failure.

**How the Watcher combines it (the poset, as a *description* of the tier logic).** The
Watcher (which has a clock) consults `episode_alive(latest_started_seq)` in its probe tier — as
a **sibling** to the handle probe, *independent of whether a handle is tracked* (the motivating
`observe()`-d cross-host run has `handle=None`, so the lock is its only definitive probe):
- lock **held** → alive (a definitive cross-host signal where `os.kill` abstains) → fall
  through to the staleness check (which still catches a *hung*-but-connected worker).
- lock **not held**, and the Watcher first saw this `started` **longer ago than a birth
  grace** → dead → `presumed_dead` (fast, definitive).
- lock **not held**, **within the grace** → inconclusive (the CAS-to-`hold_episode` window)
  → fall through to staleness.

So heartbeat-staleness remains the floor: its dead-vote is never overridden by an
alive-probe (a held lock on a worker that stopped beating still goes `presumed_dead`).
**Honest framing:** this is a *description* of the Watcher's cascade, **not** a lattice
theorem — staleness itself needs a configured `heartbeat_timeout` (off by default), and the
Watcher's two probes (`handle.is_alive` and the lock) are an antichain (incomparable
preconditions), with `resolve`/`os.kill` a different-plane probe (the claim path's). The earlier
"heartbeat is the unique minimum / the token composes for free" claim is retired; the
combination is the Watcher's explicit tier logic with the grace and the floor-veto above.

**Why the lock (vs Postgres-native alternatives).** Purpose-built: a lock bound to a session
that auto-releases on disconnect *is* connection-liveness. A heartbeat row is just staleness
in a table (not definitive); `pg_stat_activity` is indirect; external presence (ZK/etcd/Redis
TTLs) is a dependency the backend doesn't need.

**Partition / reconnect.** Clean death releases the lock instantly; a hard *partition*
releases it only when Postgres's TCP keepalive fires — so set server-side
`tcp_keepalives_idle/interval/count` (seconds, not the 2-hour default); the Watcher's
staleness backstops the gap. **Idle-reap (the inverse edge):** a long-step worker beats only
once per `tick`, so a multi-minute step leaves its connection *idle* — an `idle_session_timeout`
(common on managed PG) or a NAT idle-drop then reaps it → the lock false-releases → a healthy
worker reads `presumed_dead` and its next `send` raises. So keep the heartbeat cadence below any
idle-reap timeout, set **client-side** libpq keepalives, and avoid `idle_session_timeout` on the
worker endpoint (cost: wasted compute + a false verdict, never a double-live — the lock isn't a
claim gate). A dropped worker connection → the next `send` **raises** → the
launcher relaunches (fresh episode, fresh lock), never a silent reconnect-and-re-claim; a
fleet-wide blip therefore causes a relaunch storm (the honest single-instance failure mode).

**What this delivers / doesn't.** Delivers: definitive, fast cross-host "is this trial
dead?" for the dashboard/BO, plus a Watcher-driven cross-host death verdict. Does **not**
deliver cross-host *auto-relaunch* (re-claiming a dead foreign run) — that needs clock-aware
liveness *in the claim path*, which is the co-arbiter (deferred; not on the motivating
critical path — a BO launches a fresh trial).

## Locator + dependency

`open_channel(run_id, root=<DSN>, backend="postgres")` — `root` is the connection string (or
`RUNSTATE_PG_DSN`), a **direct or session-pooled** endpoint. Dependency: `psycopg` v3 as an
optional `runstate[postgres] = ["psycopg[binary]"]` extra; `open_channel`'s `"postgres"`
branch imports it lazily and raises a clear *"pip install runstate[postgres]"* on absence.
The `EpisodeHolder` / `EpisodeProbe` Protocols and the `isinstance`-dispatch sites live in a
**psycopg-free module** (`channel/base.py`, `watcher`), so `import runstate` works without
the extra (guarded by a CI job that imports without it).

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
table has none of `tmp_path`'s per-test freshness. The Postgres `ch` / `open_channel`
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
