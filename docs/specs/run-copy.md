# Run copy — one run's log, copied between backends at its format

**Status: IMPLEMENTED 2026-11-03**, for [#69](https://github.com/GeoffChurch/runstate/issues/69). Its shape was agreed
with GeoffChurch/mycooc on 2026-11-02, after the owner ruled that mycooc's storage tier copies rather than moves
(`../backlog/log-tiering.md`), and revised on 2026-11-03 after mycooc's review of
[#75](https://github.com/GeoffChurch/runstate/pull/75): `Logs` is the one place type, `Logs.rows`, and `drop_run`.
Built in [#76](https://github.com/GeoffChurch/runstate/pull/76) (`runstate/copy.py`, `tests/test_copy.py`). What the build
settled that this draft left open is folded in below and marked "(build)": which source a copy seals and the
same-backend pairs (§2), a writable SQLite destination (§3), the digest's field order (§4), `Row`'s module (§6)
and the liveness read (§8).

## 1. The need

mycooc keeps every run's log in Postgres, the one writable home, and writes a read-only SQLite snapshot into the
run's home each time its latest claim ends, for readers that must not depend on the server. Two copies follow:

- **Postgres → SQLite**, at every end: the snapshot.
- **SQLite → Postgres**, once per run at the cut-over: every existing log moves into Postgres, and the file left
  behind is the run's first snapshot.

Neither can be composed from the five operations. A copy must keep each record's `seq` and `created_at`, and
every append assigns the next seq and stamps the clock (and, on Postgres, the session's writer key). Without a
runstate function, a consumer would write rows into runstate's tables itself, a second encoder of the format in
a consumer, against "consumers decide content, runstate decides encoding".

`runstate migrate` does not do it either. It moves a log forward a *format* within *one* store and seals the old
log behind it; a copy keeps the format and crosses *backends*. The two compose (copy, then migrate) and neither
subsumes the other.

## 2. The operation

```python
copy_run(run_id: str, *, source: Logs, destination: Logs) -> Copied

class Copied(NamedTuple):
    digest: str     # log_digest of the rows copied (§4)
    last_seq: int   # the newest seq copied
```

`Logs` is a place where a backend keeps logs at the current format: `SqliteLogs(root)`, the directory the
locators take as `root`, and `PostgresLogs(dsn)`. Both endpoints are at `LOG_FORMAT`; any other format is
`runstate migrate`'s. A consumer builds one `Logs` per place, where it reads its configuration, and passes it
down (#73 adds the channel-opening side to the same classes, §7).

A `Logs` also reads a run's rows, so a caller can compute `log_digest` (§4) on either side without copying:

```python
Logs.rows(run_id: str) -> list[Row]   # in seq order, in one read; RunNotFound if the run has none
```

A run's last seq is its channel's `last_seq()`, through `attach_channel` today.

The refusals are typed by what the caller does next:
- `RunLive`: the run's latest claim has not ended. This is routine, not an error (§3).
- `RunNotFound`, the locators' own: the source holds no log for the run.
- `CopyRefused`: the destination holds rows the source does not extend (§2, step 3). An append-only log never
  diverges from its own copy, so this one is an error.

In order:

1. **Read the source** in one read: a single statement on Postgres, which is one snapshot. A SQLite source is
   sealed first (§3), and read after the seal.
2. **Refuse a live run**: one whose latest claim has not ended (`live_episode`), read from the rows read. Refuse
   a run with no log at the source.
3. **Check the destination**, then write (§3). A destination that already holds the run must hold a prefix of the
   source's rows, compared row by row; anything else is refused, since an append-only log never diverges from its
   own copy.
4. **Verify**: re-read the destination's rows and compare their digest with the source's.
5. **Return** the digest and the last seq.

**Which source is sealed (build).** A run has exactly one writable log, and every copy of it is sealed. So the
source is sealed exactly when the destination is writable, which is Postgres: a copy into SQLite is a sealed
snapshot and leaves its source as it was. Each `Logs` says which it is, and `copy_run` asks no other question. The
two pairs this draft did not name follow from it: **SQLite → SQLite** writes a sealed snapshot and leaves the
source writable, and **Postgres → Postgres** is refused with `ValueError` before anything is read, since a
Postgres log cannot be sealed while it is worked on (#70) and the run would have two writable logs.

**Both endpoints take the locators' opening checks (build)** (`log-formats.md` §4): a root or database holding a
newer format, or the run held in an older format or at the legacy address, raises `LogFormatMismatch` or
`LogFormatMissing` on either side. A copy is never written beside a log of another format.

Every field copies exactly. Format 0.4.0 stores `body` as text and `created_at` as a double on both backends
(`runstate/formats/v0_4_0.py`), so a row reads back byte-identical whichever way it went.

On Postgres, `writer_key` and `writer_server` land NULL: a copied row carries no session evidence, which
`PostgresChannel.writer_session` already reads as such, so the session probe abstains on it. SQLite has no
writer stamp.

## 3. Per direction

**Postgres → SQLite (a snapshot).**
- The source is not sealed: Postgres stays the run's writable log.
- The destination is the locators' address, `<root>/v0.4.0/<rid>.db`. It may be absent, equal to the source
  (a no-op, so a retry succeeds), or a strict prefix of it (replaced). A destination that is not sealed is the
  run's writable SQLite log, and a copy never replaces it or adopts it as a snapshot: `CopyRefused` (build).
- The file is written under a hidden temporary name beside the address, verified, sealed by `seal_sqlite`
  (checkpointed, `journal_mode=DELETE`, every write bit cleared), and renamed into place. A reader never sees a
  partial or writable snapshot, and a replacement needs write permission on the directory only.
- A claim that lands between the run's end and the export's read makes the export raise `RunLive`. That is
  routine: the run is live again, and its next end snapshots it. A caller treats it as "nothing to do now".
- A claim that lands after the read leaves the snapshot stale on arrival. Under copy-not-move that costs only
  freshness, until the run next ends. The returned `last_seq` lets the caller compare with the server's
  `last_seq()` after the rename and retire a stale snapshot; mycooc does, and its health sweep catches the rest.
  The export never publishes a row from after its read, since the read is one statement.
- A sealed snapshot is one file. The seal checkpointed it and set `journal_mode=DELETE`, so it has no `-wal` or
  `-shm` beside it, and renaming or replacing it needs write and search permission on its directory and nothing
  on the file. (On a directory with the sticky bit set, only the file's owner, the directory's owner or root may
  rename it, as for any file.)

**SQLite → Postgres (the cut-over import).**
- The source is sealed before its read, as `runstate migrate` seals: read once, refuse a live run, seal, read
  again. This is the one place under copy-not-move where a log has two homes and a stale writer, one not stopped
  before the cut-over, could reach the old one. Sealed, its next append fails loudly instead of forking the run.
  The sealed file is then the run's first snapshot.
- The destination is the format's schema (`runstate_v0_4_0`), born with the format's DDL under the same advisory
  lock a channel's birth takes. It may be absent or equal to the source (a no-op, so a retried import
  succeeds). A strict prefix is refused: Postgres is the writable log, and a writer may be appending to it.
- The rows are inserted, checked and committed in one transaction. A writer birthing the run concurrently
  collides with the insert on the primary key `(run_id, seq)`, and one of the two fails: a claim's CAS returns
  `None`, as any loser's does, and a losing import raises `CopyRefused`.
- The schema is born first, by `ensure_schema`, as a channel's birth provisions it, in a transaction of its own
  (build): the birth lock is then not held across the import's inserts, and a rolled-back import leaves only the
  schema any channel would have born.
- A crash after the seal and before the commit leaves the run sealed in SQLite and absent from Postgres: no
  writable log, nothing lost. A retry seals nothing new and completes.

## 4. The digest

Public, so a caller can recompute it from either side without copying:

```python
log_digest(rows: Iterable[Row]) -> str   # "sha256:<64 hex digits>"
```

`Row` is `(seq, topic, name, request_id, body, created_at)`, the columns every format-0.4.0 log holds. The digest
is SHA-256 over the rows in seq order (rows out of order are a `ValueError`), each encoded as the concatenation of
its fields **in that order** (build), each field as:

| Field | Encoding |
|---|---|
| `seq` | 8 bytes, big-endian two's complement |
| `topic`, `body` | 4-byte big-endian length, then the UTF-8 bytes |
| `name`, `request_id` | `0x00` if NULL; else `0x01`, the 4-byte length, then the UTF-8 bytes |
| `created_at` | 8 bytes, the IEEE 754 binary64 value, big-endian |

A length prefix and a byte-exact float make the encoding unambiguous and language-neutral: no JSON escaping, no
float formatting. The run id is not in it, since the digest describes a log's contents, wherever it lives.
Fixed vectors, with their expected digests written out, are in `tests/test_copy.py`
(`test_log_digest_fixed_vectors`, and the bytes of two rows in `test_log_digest_encodes_each_row_as_the_table_says`).

A destination is compared with the source row by row as this encoding writes them, so a prefix of rows is a prefix
of the digest's input.

## 5. Dropping a run's log

Under copy-not-move, a consumer's GC deletes a run's home and its rows in Postgres together. Deleting rows from
runstate's table is the same second-encoder problem §1 rules out for writes, so the module also offers:

```python
drop_run(run_id: str, *, logs: Logs) -> None
```

- **It refuses a live run** (`RunLive`), as `store.md`'s GC recipe does.
- **It is idempotent:** an absent run is a no-op.
- **On SQLite**, it deletes the log at its address with any `-wal`, `-shm` or `-journal` beside it, as the GC
  recipe says.
- **On Postgres**, it reads the rows, checks liveness and deletes them in one transaction, under the table lock
  the seal takes (`SHARE ROW EXCLUSIVE`). Without it, an append whose statement began before the delete committed
  could compute `MAX(seq) + 1` from the deleted rows and land one orphan row at seq N+1. Under the lock, an
  in-flight append finishes first, and a later one sees no rows: a compare-and-append expecting N fails, as any
  CAS loser does.
- **Its limit:** a writer still holding a channel to the run that appends unconditionally after the drop births
  a new log at seq 1, as one would on SQLite after the file is deleted. The live refusal bounds this to a
  non-claim write (a stop, a death report) to a run already collected.

## 6. Placement and surface

- A module of its own, `runstate/copy.py`, beside `channel/` rather than under `migrations/`, since it is not a
  migration. It reuses `migrations/seal.py`'s seals and the channel modules' DDL and birth lock.
- `copy_run`, `drop_run`, `Copied`, `log_digest`, `Row`, `Logs`, `SqliteLogs`, `PostgresLogs`, `RunLive` and
  `CopyRefused` join `runstate.__all__`.
- **`Row` moves to `runstate.formats` (build),** not to `runstate/copy.py`: it is the record of every registered
  format's log table, which the migrations read and write for every format and the copy for the current one, so
  both import it from the leaf they already depend on. In `copy.py` it would point the migrations at the copy, and
  close an import cycle, since the copy reuses `runstate.migrations.seal`. `runstate.migrations` imports it for its
  own use, and its retained steps keep taking it from there (`from . import Row`), unchanged. `copy.py` imports the
  seal at its call, so `import runstate` does not load every retained step.
- The SQLite opening checks move out of the locator into `runstate.channel.sqlite_log_path(root, run_id)`, which
  the locators and the copy share; Postgres's were already `channel.postgres.check_format`.
- No command line: mycooc wraps its bulk import in a module of its own, which owns resumability, the per-run
  report and its fold-identical check.

## 7. Changes elsewhere

- **`log-formats.md` §2 rule 6** says how a migrated run keeps one writable log. It gains the copy's case: a run
  has exactly one writable log, and every copy of it is sealed.
- **`log-tiering.md`** cites this spec as the primitive mycooc's tier uses.
- **#70 stays closed:** no live Postgres seal, no unseal, and format 0.4.0's DDL is unchanged.
- **`store.md`'s GC recipe** gains the Postgres case: collecting a run there is `drop_run`.
- **#73** extends `Logs` with the channel-opening side, rather than adding a parallel type. Two types naming one
  place would let a caller pair an opener on one DSN with a `Logs` on another, and nothing would catch it
  (mycooc's review, 2026-11-03). #69 does not wait on #73.

## 8. Open questions

1. **#74's read-only attach.** A sealed snapshot already attaches, reads, and refuses a write
   (`sqlite3.OperationalError: attempt to write a readonly database`, checked 2026-11-03). Opening it with
   `immutable=1`, and a read-only handle type, are #74's.
2. ~~**The liveness read.**~~ Settled by the build: the rows become envelopes in a `MemoryLog`, and `live_episode`
   reads it through a `MemoryChannel`, so the copy and the claim gate cannot disagree.

## 9. Tests

- Each direction round-trips byte-identically, over the backends' conformance fixtures: every row, seq and
  `created_at` equal, the digests equal.
- A copied Postgres run carries NULL writer stamps, and `writer_session` abstains on its rows.
- Refusals: a live run; no log at the source; a destination that diverges; a strict prefix in Postgres.
- Retries: an equal destination is a no-op in both directions; a strict-prefix snapshot is replaced.
- The snapshot is sealed when published, and a temporary file never survives a failed write.
- The import seals its source: a writer to the SQLite address afterwards fails loudly.
- A writer birthing the run in Postgres during an import: exactly one of them commits.
- After an import, the next append lands at `last_seq + 1`.
- An export racing a new claim either raises `RunLive` or publishes a snapshot whose last seq is from before
  the claim; it never publishes a row from after it.
- `Logs.rows` equals what `copy_run` copies, on each backend, and `log_digest` of each side's rows agree.
- `drop_run`: refuses a live run; an absent run is a no-op; on SQLite the sidecars go too; on Postgres an append
  racing the drop leaves no orphan row, and a CAS expecting the dropped run's last seq fails.
- `log_digest` against fixed vectors (NULLs, non-ASCII text, an empty body, negative zero), so another language
  can check its implementation.
