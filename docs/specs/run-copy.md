# Run copy — one run's log, copied between backends at its format

**Status: DRAFT 2026-11-03**, for [#69](https://github.com/GeoffChurch/runstate/issues/69). Its shape was agreed
with GeoffChurch/mycooc on 2026-11-02, after the owner ruled that mycooc's storage tier copies rather than moves
(`../backlog/log-tiering.md`). Not built.

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

`Logs` is where a backend keeps logs at the current format: `SqliteLogs(root)`, the directory the locators take
as `root`, and `PostgresLogs(dsn)`. Both endpoints are at `LOG_FORMAT`; any other format is `runstate migrate`'s.

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

Every field copies exactly. Format 0.4.0 stores `body` as text and `created_at` as a double on both backends
(`runstate/formats/v0_4_0.py`), so a row reads back byte-identical whichever way it went.

On Postgres, `writer_key` and `writer_server` land NULL: a copied row carries no session evidence, which
`PostgresChannel.writer_session` already reads as such, so the session probe abstains on it. SQLite has no
writer stamp.

## 3. Per direction

**Postgres → SQLite (a snapshot).**
- The source is not sealed: Postgres stays the run's writable log.
- The destination is the locators' address, `<root>/v0.4.0/<rid>.db`. It may be absent, equal to the source
  (a no-op, so a retry succeeds), or a strict prefix of it (replaced).
- The file is written under a hidden temporary name beside the address, verified, sealed by `seal_sqlite`
  (checkpointed, `journal_mode=DELETE`, every write bit cleared), and renamed into place. A reader never sees a
  partial or writable snapshot, and a replacement needs write permission on the directory only.
- A claim that lands after the read leaves the snapshot stale on arrival. Under copy-not-move that costs only
  freshness, until the run next ends. The returned `last_seq` lets the caller compare with the server's
  `last_seq()` after the rename and retire a stale snapshot; mycooc does, and its health sweep catches the rest.

**SQLite → Postgres (the cut-over import).**
- The source is sealed before its read, as `runstate migrate` seals: read once, refuse a live run, seal, read
  again. This is the one place under copy-not-move where a log has two homes and a stale writer, one not stopped
  before the cut-over, could reach the old one. Sealed, its next append fails loudly instead of forking the run.
  The sealed file is then the run's first snapshot.
- The destination is the format's schema (`runstate_v0_4_0`), born with the format's DDL under the same advisory
  lock a channel's birth takes. It may be absent or equal to the source (a no-op, so a retried import
  succeeds). A strict prefix is refused: Postgres is the writable log, and a writer may be appending to it.
- The rows are inserted, checked and committed in one transaction. A writer birthing the run concurrently
  collides with the insert on the primary key `(run_id, seq)`, and one of the two fails.
- A crash after the seal and before the commit leaves the run sealed in SQLite and absent from Postgres: no
  writable log, nothing lost. A retry seals nothing new and completes.

## 4. The digest

Public, so a caller can recompute it from either side without copying:

```python
log_digest(rows: Iterable[Row]) -> str   # "sha256:<64 hex digits>"
```

`Row` is `(seq, topic, name, request_id, body, created_at)`, the columns every format-0.4.0 log holds. The digest
is SHA-256 over the rows in seq order, each encoded as the concatenation of:

| Field | Encoding |
|---|---|
| `seq` | 8 bytes, big-endian two's complement |
| `topic`, `body` | 4-byte big-endian length, then the UTF-8 bytes |
| `name`, `request_id` | `0x00` if NULL; else `0x01`, the 4-byte length, then the UTF-8 bytes |
| `created_at` | 8 bytes, the IEEE 754 binary64 value, big-endian |

A length prefix and a byte-exact float make the encoding unambiguous and language-neutral: no JSON escaping, no
float formatting. The run id is not in it, since the digest describes a log's contents, wherever it lives.

## 5. Placement and surface

- A module of its own, `runstate/copy.py`, beside `channel/` rather than under `migrations/`, since it is not a
  migration. It reuses `migrations/seal.py`'s seals and the channel modules' DDL and birth lock.
- `copy_run`, `Copied`, `log_digest`, `Row`, `Logs`, `SqliteLogs` and `PostgresLogs` join `runstate.__all__`.
  `Row` moves from `runstate.migrations` to the new module, which the migrations then import.
- No command line: mycooc wraps its bulk import in a module of its own, which owns resumability, the per-run
  report and its fold-identical check.

## 6. Changes elsewhere

- **`log-formats.md` §2 rule 6** says how a migrated run keeps one writable log. It gains the copy's case: a run
  has exactly one writable log, and every copy of it is sealed.
- **`log-tiering.md`** cites this spec as the primitive mycooc's tier uses.
- **#70 stays closed:** no live Postgres seal, no unseal, and format 0.4.0's DDL is unchanged.

## 7. Open questions

1. **`Logs` against #73's opener.** #73 asks for an injected Strategy that opens a run's channel, in place of the
   `(root, backend)` pair. `SqliteLogs(root)` and `PostgresLogs(dsn)` say where a backend keeps logs, which is
   what such an opener holds. If #73 lands as an opener over the same two places, these could be the same
   objects, with `copy_run` using a row-level side the channels do not expose. Decide with #73, or ship `Logs`
   now and let #73 extend it.
2. **#74's read-only attach.** A sealed snapshot already attaches, reads, and refuses a write
   (`sqlite3.OperationalError: attempt to write a readonly database`, checked 2026-11-03). Opening it with
   `immutable=1`, and a read-only handle type, are #74's.
3. **The liveness read.** Step 2 needs `live_episode` over rows rather than a channel. Building envelopes from the
   rows and reusing the fold is the expected shape; this is for the build.

## 8. Tests

- Each direction round-trips byte-identically, over the backends' conformance fixtures: every row, seq and
  `created_at` equal, the digests equal.
- A copied Postgres run carries NULL writer stamps, and `writer_session` abstains on its rows.
- Refusals: a live run; no log at the source; a destination that diverges; a strict prefix in Postgres.
- Retries: an equal destination is a no-op in both directions; a strict-prefix snapshot is replaced.
- The snapshot is sealed when published, and a temporary file never survives a failed write.
- The import seals its source: a writer to the SQLite address afterwards fails loudly.
- A writer birthing the run in Postgres during an import: exactly one of them commits.
- After an import, the next append lands at `last_seq + 1`.
- `log_digest` against fixed vectors (NULLs, non-ASCII text, an empty body, negative zero), so another language
  can check its implementation.
