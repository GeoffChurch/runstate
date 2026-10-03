# Spec: log formats — a log's format is part of its address

**Status:** IMPLEMENTED 2026-10-03 (stage 1: format 0.2.0, the addresses, the opening checks, sealing and
`runstate migrate`). Designed with the owner section by section. Its first
user is [`reference-by-name.md`](reference-by-name.md), which introduces format 0.3.0.

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

## 4. Opening a log

`attach_channel` and `create_channel` both run these checks, in order, before anything reads or writes.
Every party opens a log through one of them: the Worker, the folds, `ensure`, the Watcher, and raw
`channel.send` users.

1. **A newer format is present.** If the root holds any format newer than `LOG_FORMAT` (a `vX.Y.Z/`
   directory, or a `runstate_v…` schema), raise **`LogFormatMismatch`**, telling the caller to upgrade
   runstate. A newer format's interior cannot be known, but its name can, so this needs one directory or
   schema listing, cacheable per process. Without it an old runstate would read "not found" and relaunch a
   run that has moved on.
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
  concurrent inserts for that run.

*Found in implementation.* SQLite sealing **raises rather than partially sealing** when another
connection holds the log open. And sealing by file permissions **does not bind the root user**, who can
still write a read-only file; the seal is a guard against the wrong upgrade order, not against root.

Sealing is what makes the wrong upgrade order fail loudly. A pinned writer from before format versioning
never checks a format, but its append to a sealed log is refused by the file system or the database.

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
  2. Seal the old log.
  3. Transform into the new format's address. On SQLite, write to a temporary file in the target directory
     and rename it into place; on Postgres, use the sealing transaction.
- **A failure leaves the run sealed and unmigrated.** New code then raises "migrate"; old code cannot
  write; re-running `runstate migrate` completes it. A chain that stops part-way leaves the run at an
  intermediate format, and re-running continues from there.
- **Steps are retained, never deleted.** This resolves `release-and-stability-contract.md` §(b) in favour
  of its option 3, with the detection problem removed by the address.

*Found in implementation.* The CLI is `runstate migrate <root> [<rid>...] [--to V] [--backend
sqlite|postgres]`. On Postgres the seal is taken first, then the rows are read and copied **in the same
transaction**, so any failure rolls back completely and **leaves the run unsealed and unmigrated**: stricter
than SQLite, where a failure leaves the run sealed and unmigrated. Re-running `runstate migrate` completes
either.

## 7. Onboarding legacy logs

Logs written before format versioning sit at the legacy address. Nothing infers their format. The
`LogFormatMissing` message tells the user:

> This log predates versioned addresses. If it was written by runstate at or after `4729fcd`
> (2026-07-16, lifecycle-v0.4 and launcher-v0.4), it is format 0.2.0 (check: its `lifecycle.heartbeat`
> bodies carry a `t` field). Move it to `<root>/v0.2.0/<rid>.db`, then run `runstate migrate <root>`.

It also gives a one-line shell loop for moving every legacy log under a root. On Postgres the instruction is
`ALTER TABLE log SET SCHEMA runstate_v0_2_0` (after creating the schema).

**The move is the user's explicit statement of the format.** `runstate migrate` has no legacy option.

**The legacy check (rule 4 in §4) is the only legacy code, and it has a deletion trigger:** delete it once
every consumer has onboarded. Until then it must stay, because without it a legacy run would read as "not
found" and be relaunched from scratch.

## 8. Operating rules

- **The upgrade procedure for a consumer:** bump its pin, then migrate its logs, in that order. If the
  order is wrong, sealing makes the old writer fail loudly.
- **Rollback:** make the old log writable again (`chmod u+w`, or delete the run from `sealed_runs`),
  delete the new one, and revert the pin.
- **Garbage collection:** a sealed log may be deleted once nothing pins its format. This is an owner
  action. `store.md` Recipe 3 gains a rule: collecting a run deletes its log at its format's address,
  because a log no longer sits inside the run's home.
- **Copying a run** between roots or backends copies its log at its format's address, which carries the
  format with it.

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
