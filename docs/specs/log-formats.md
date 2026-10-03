# Spec: log formats — a log's format is part of its address

**Status:** IMPLEMENTED 2026-10-03: format 0.2.0, the addresses, the opening checks, sealing, and
`runstate migrate`, which refuses a run before sealing it (§6). Designed with the owner section by section.
Its first user is [`reference-by-name.md`](reference-by-name.md), which introduced format 0.3.0, the
current one, with the first retained step (0.2.0 → 0.3.0) the same day.

**What it gives:** every log has exactly one format, and every log can say which. A runstate release opens
only logs of its own format and raises on any other. Logs move between formats only through retained,
chained migration steps that copy, never rewrite.

## 1. The problem

Until now a convention bump was handled by an owner-run script that rewrote the author's logs in place,
then was deleted once it converged. That worked while one author held every log. It stops working on two
counts.

**Consumers now upgrade on their own schedule.** mycooc, translation and runstate-tui pin runstate to a
commit (2026-10-03), so a log can sit at an old format indefinitely. Whatever moves it forward must still
exist when its owner upgrades. `../backlog/release-and-stability-contract.md` §(b) anticipated this, and
named the missing piece as "version detection, a step ladder".

**A log's format cannot be told from its shape.** `value.t` was redefined from per-episode to absolute
time, with the same field and the same shape (`memoizer.md`). Some future changes will be undetectable by
inspection. A step ladder needs to know where to start, so the format must be stated, not inferred.

## 2. The rules

1. **A log has exactly one format**, named by **the runstate release that introduced it**: a version
   string such as `"0.3.0"`. It is never optional, never nullable, and never inferred.
2. **A release knows one format,** `runstate.LOG_FORMAT`: the version of the last release that changed
   the format. Releases that do not change the format inherit it (0.3.1 still writes and reads `0.3.0`).
3. **A format changes in exactly one commit,** together with the package-version bump. Spike branches
   never introduce a new format string.
4. **The format is the outermost part of a log's address.** Each format owns its layout below that
   point, so a later format may change anything there: one file per run, a shared database, cross-run
   metadata.
5. **No mixed logs.** A log is entirely in one format. Nothing reads one format as another.
6. **Exactly one writable log per run.** A run whose log is being migrated, or has been, has a sealed old
   log and at most one writable new one.

**Why the address, and not a record or metadata.** A record would make a created-but-unclaimed log hold
records, which changes the existence rule (`channel-locators.md`: a run exists iff it has records). It
would also put a fact about the container into the run's history. In-file metadata would need a format
table in every log, an atomic stamp at creation, and a per-run notion on Postgres's shared table, which
cannot describe a change to that table's structure. An address needs none of these, and makes migration
non-destructive. A **directory per format** rather than a per-run directory (`<root>/<rid>/v0.3.0.db`)
because only the outermost component leaves every future layout free.

## 3. Addresses

| backend | format 0.2.0 | format 0.3.0 | legacy (unversioned) |
|---|---|---|---|
| SQLite | `<root>/v0.2.0/<rid>.db` | `<root>/v0.3.0/<rid>.db` | `<root>/<rid>.db` |
| Postgres | schema `runstate_v0_2_0`, table `log` | schema `runstate_v0_3_0`, table `log` | table `log` outside any `runstate_v…` schema |
| Memory | — | — | — |

- **Directory and schema names** are `v` plus the version, and `runstate_v` plus the version with dots as
  underscores.
- **Each format's layout lives in its own module,** `runstate/formats/v0_2_0.py` and so on. It is a
  function from (root, run id) to an address for each persistent backend. The runtime uses only the
  current format's module. The others exist for the opening checks and the migration steps.
- **Memory logs are in-process and ephemeral,** so they are always in the current format and carry no
  address.
- **The legacy Postgres table** is the `log` that an unqualified name resolves to under the connection's
  own search path, because every statement of a runstate from before versioned addresses was unqualified.
  That is `public.log` by default, and another schema when the DSN carries
  `options=-csearch_path=...`. It counts only outside the `runstate_v…` schemas, and only if it has a
  `run_id` column: another application's `log` is not runstate's. *Found in the final review:* the check
  first looked only in `public`, so a run in another schema read as not found, and `create_channel`
  birthed an empty run over it; and an unrelated `public.log` raised `UndefinedColumn` on every open.

## 4. Opening a log

`attach_channel` and `create_channel` both run these checks, in order, before anything reads or writes.
Every party opens a log through one of them: the Worker, the folds, `ensure`, the Watcher, and raw
`channel.send` users.

1. **A newer format is present.** If the root holds any format newer than `LOG_FORMAT` (a `vX.Y.Z/`
   directory, or a `runstate_v…` schema), raise **`LogFormatMismatch`**, telling the caller to upgrade
   runstate. A newer format's interior cannot be known, but its name can, so this needs one directory or
   schema listing, cacheable per process. Without it an old runstate would read "not found" and relaunch a
   run that has moved on. On Postgres the listing covers the **whole database**, so consumers that share
   one database upgrade in lockstep (§8).
2. **The current format's address exists.** Open it.
3. **An older format's address holds the run.** Older layouts come from the format modules. Raise
   **`LogFormatMismatch`**, naming the found and expected formats and telling the caller to run
   `runstate migrate`.
4. **The legacy unversioned address holds the run.** Raise **`LogFormatMissing`**. Its message carries the
   one-time onboarding instructions (§7).
5. **Nothing anywhere.** `attach_channel` raises `RunNotFound`. `create_channel` births the log at the
   current address.

Both errors subclass a new **`LogFormatError`**. Neither is ever caught and turned into "not found".
Memory channels have no address, so they skip these checks.

## 5. Sealing

A sealed log is readable and refuses every write.

- **SQLite.** Checkpoint, switch the file to `journal_mode=DELETE` so that it opens read-only without a
  writable `-wal` or `-shm`, then make the file read-only. An old writer's next append fails with
  "attempt to write a readonly database".
- **Postgres.** The old schema gains a `sealed_runs(run_id)` table and an insert-refusing trigger on its
  `log` table. Sealing a run and copying it happen in one transaction, holding a lock that blocks
  concurrent inserts for that run (§6 gives the failure behavior).

*Found in implementation.* SQLite sealing **raises rather than partially sealing** when another
connection holds the log open. And sealing by file permissions **does not bind the root user**, who can
still write a read-only file; the seal is a guard against the wrong upgrade order, not against root.

Sealing is what makes the wrong upgrade order fail loudly for a writer that resolves the sealed log's
address: its append is refused by the file system or the database.

**A writer from before format versioning never resolves a versioned address.** It checks no format and
opens only the legacy address (§3), which onboarding (§7) empties by moving the log away, so the seal on the
moved copy never reaches it. Onboarding therefore leaves a **tombstone** at the legacy address, which plays
the seal's part for such a writer:

- **SQLite.** An empty, read-only `<root>/<rid>.db`. The old writer's create fails with "attempt to write a
  readonly database", in WAL and DELETE mode alike, and its attach finds no records (`RunNotFound`).
- **Postgres.** An empty `log` with the old columns, in the schema the legacy table was found in, and a
  trigger on it that refuses every insert. The old `ensure_schema` finds the table and creates nothing, and
  the old writer's first append raises.

*Found in the final review.* Without the tombstone the wrong order did not fail at all. A pre-versioning
worker started after onboarding saw no records at the legacy address, birthed a fresh log there (on
Postgres, its `ensure_schema` recreated `log`), recomputed the run from step 0, and finished without an
error.

The tombstone is part of the onboarding instructions, not runtime code. This release's opening checks reach
an onboarded run through checks 2 and 3 (§4) before they look at the legacy address, so they answer as they
would without it, before `runstate migrate` and after; tests pin both backends.

## 6. `runstate migrate`

```
runstate migrate <root> [<rid> ...] [--to V]
```

`<root>` is a directory for SQLite and a DSN for Postgres. With no run ids, every run under the root is
migrated.

- **Steps** live in `runstate/migrations/`, one module per step, each declaring `FROM` and `TO` format
  versions and a transform from a run's records in `FROM` to its records in `TO`.
- **The runner chains steps** from each log's format to `--to`, which defaults to `LOG_FORMAT`. It refuses
  if there is no path, or if two steps leave the same format.
- **Each step, per run:**
  1. Refuse if the run has a live episode, read with the `FROM` format's semantics; a step carries what it
     needs of its old format. An unresolvable foreign claim reads as live, so a stranded claim must be
     released first.
  2. Refuse if the step refuses the run's records. A step's refusal is deterministic, so it comes before
     the seal: on SQLite the runner dry-runs the transform on the records it read for step 1.
  3. Seal the old log.
  4. Transform into the new format's address. On SQLite, write to a temporary file of its own in the target directory
     and rename it into place; on Postgres, use the sealing transaction.
- **A refusal leaves the run untouched.** Every refusal is a `MigrationError` that names its run
  (`run '<rid>': …`), and `migrate` stops at the first. The refused run stays writable and unmigrated, and
  a re-run refuses it the same way. One residual on SQLite: records that land between the pre-seal read
  and the seal can still make the post-seal transform refuse, which leaves the run sealed and unmigrated;
  recovery is §8's rollback. On Postgres there is no such window, because the seal is taken before the
  read.
- **A failure is recoverable by re-running `runstate migrate`, which completes it.** On SQLite a failure
  after the seal leaves the run sealed and unmigrated: new code raises "migrate", and old code cannot
  write. On Postgres the seal and the copy are one transaction, so a failure, a refusal included, rolls
  back completely and leaves the run unsealed and unmigrated. A chain that stops part-way leaves the run
  at an intermediate format, and re-running continues from there.
- **Steps are retained, never deleted.** This resolves `release-and-stability-contract.md` §(b) in favor
  of its option 3, with the detection problem removed by the address.

*Found in implementation.* The CLI is `runstate migrate <root> [<rid>...] [--to V] [--backend
sqlite|postgres]`. On Postgres the seal is taken first, then the rows are read and copied in that one transaction.

*Found in the final review.* `runstate migrate` exited 0 having done nothing when the root did not exist,
or when a DSN was given without `--backend postgres` and read as a SQLite root with no runs. It refuses
both now: a SQLite root that is not a directory, and a `postgres://` or `postgresql://` root without
`--backend postgres`. Every message that says to migrate gives the whole command, `runstate migrate
<root>` or `runstate migrate '<dsn>' --backend postgres` (a placeholder, since a DSN can carry a
password).

## 7. Onboarding legacy logs

Logs written before format versioning sit at the legacy address. Nothing infers their format. The
`LogFormatMissing` message tells the user:

> This log predates versioned addresses. If it was written by runstate at or after `4729fcd`
> (2026-07-16, lifecycle-v0.4 and launcher-v0.4), it is format 0.2.0 (check: its `lifecycle.heartbeat`
> bodies carry a `t` field). First stop every process that writes under the root, and drain any queued
> jobs that would. Then move it, with its `-wal`, `-shm` and `-journal` files, to `<root>/v0.2.0/<rid>.db`,
> leave a tombstone at its old address, and run `runstate migrate <root>`.

It also gives a one-line shell loop that does this for every legacy log under a root. The loop skips empty
files and logs already in place, so running it again is safe.

*Found in the final review.* A writer in a rollback journal mode (DELETE, which mycooc uses on NFS) that
died mid-transaction leaves a hot `-journal` holding the pages it overwrote; the log is consistent only
once the journal rolls back. So the loop moves the `-journal` with its log, and `runstate migrate`'s first
read, before the seal, opens the log read-write, because a read-only open cannot roll a hot journal back. On Postgres the instruction is one
transaction: create schema `runstate_v0_2_0`, `ALTER TABLE <schema>.log SET SCHEMA runstate_v0_2_0`, then
create the tombstone (§5) in the schema the table was found in.

**The tombstone is why the move is not enough.** A writer still running a release from before versioned
addresses resolves only the legacy address. Without the tombstone it would find nothing there and start the
run over, silently; with it, its next write fails (§5).

**The move is the user's explicit statement of the format.** `runstate migrate` has no legacy option.

**The legacy check (rule 4 in §4) is the only legacy code, and it has a deletion trigger:** delete it once
every consumer has onboarded. Until then it must stay, because without it a legacy run would read as "not
found" and be relaunched from scratch.

## 8. Operating rules

- **The upgrade procedure for a consumer:** stop its old processes and drain its queued jobs, bump its
  pin, onboard its legacy logs (§7), then migrate them, in that order. A job queued under the old pin runs
  the old code whenever it starts. If the order is wrong anyway, the seal and the tombstone (§5) are the
  backstop: they make the old writer fail loudly rather than start the run over.
- **Rollback:** make the old log writable again (`chmod u+w`, or delete the run from `sealed_runs`),
  delete the new one, and revert the pin. Reverting to a pin from before versioned addresses also undoes
  the onboarding: delete the tombstone, and move the log back to the legacy address.
- **Garbage collection:** a sealed log may be deleted once nothing pins its format. This is an owner
  action. `store.md` Recipe 3 gains a rule: collecting a run deletes its log at its format's address,
  because a log no longer sits inside the run's home, and deletes its tombstone at the legacy address with
  it. A tombstone left behind would make the run's id read as an unonboarded legacy log (§4, check 4).
- **Copying a run** between roots or backends copies its log at its format's address, which carries the
  format with it.
- **Consumers that share one Postgres database upgrade in lockstep.** The newer-format check (§4, check
  1) covers the whole database: once one consumer migrates, the new format's schema exists, and every
  open by a consumer still pinned to the old release raises `LogFormatMismatch` until it upgrades too.

## 9. Interop

The address scheme and the opening checks are part of the protocol. Another language's implementation must
compute addresses the same way and raise on the same five cases. The conformance suite gains tests for each
(§10).

## 10. Tests

On SQLite and Postgres (Memory has no addresses):

- the five opening cases (§4), including refusing a root that holds a newer format, and the legacy
  "missing" error carrying its instructions;
- an old-style writer appending to a sealed log fails loudly;
- chaining through test-only steps (`FROM`→`TO` edges, no-path and two-steps-from-one-format refusals);
- a migration that fails midway leaves the run sealed and unmigrated, and a retry completes it;
- refusing a run with a live episode;
- the atomic publish: no reader ever sees a partial new log.

## 11. Ripple

- `channel-locators.md`: the address scheme and §4's checks.
- `channel-postgres.md`: a schema per format.
- `store.md`: a log's address under placement, and Recipe 3's deletion rule.
- `design-v0.2.md` §4: the locators check formats.
- `CLAUDE.md`: the upgrade procedure, beside the pinning note.
- `../backlog/release-and-stability-contract.md` §(b): resolved by this spec.

## Related

- [`reference-by-name.md`](reference-by-name.md) — introduces format 0.3.0, and the first migration step.
- [`channel-locators.md`](channel-locators.md) — the locators this spec extends.
- [`../backlog/identity-in-records.md`](../backlog/identity-in-records.md) — the design both specs serve.
