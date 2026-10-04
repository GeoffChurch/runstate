# Log formats and reference by name: follow-ups

**Status:** LIVING (opened 2026-10-03). Every item was found during the review of the branch that shipped
[`../specs/log-formats.md`](../specs/log-formats.md) and
[`../specs/reference-by-name.md`](../specs/reference-by-name.md) (log format 0.3.0). The final review judged each
one still real but not worth holding the merge for. None is a correctness defect on any path a caller can reach
today. An item leaves this list by being done, or by being shown moot.

## Cost on every open

- **The directory listing and the `pg_namespace` scan are uncached.** Every `attach_channel` and `create_channel`
  lists the root to look for newer formats (`log-formats.md` §4, check 1). On SQLite it also `is_dir`s every entry
  before the name filter. On Postgres it scans `pg_namespace`. The spec says the result is cacheable per process.
  Worth doing when a root holds thousands of runs and something opens many of them in a loop, as a cockpit would.
- **`PostgresStore` lists run ids with `DISTINCT run_id` and filters them in Python.**

## Migration robustness

- **`chain` has no cycle guard.** It is latent while one step exists.
- **`PostgresStore` duplicates the log DDL without the advisory lock the channel takes, and imports private names.**
  Two migrators racing `CREATE TABLE IF NOT EXISTS` could collide. Migration is offline by spec.
- **The CLI prints a traceback on a `MigrationError`.** The seal's `RuntimeError` and raw `sqlite3` errors are not
  `MigrationError`s, so they surface raw too. A clean one-line message per refusal is the fix.
- **SQLite runs the transform twice**, as a dry run before sealing and again for real (Ruling 12). This costs
  offline time only. The alternative, sealing first and restoring the write bit on refusal, was considered and
  declined. It would remove the residual window, but only by adding a restore path that can itself fail.

## Tests that would pin more

- No re-seal test, and no Postgres test that a concurrent insert blocks on the seal's lock. The transaction contract
  lives only in a docstring.
- The Postgres "read after seal" order is not strictly pinned.

## Unreachable today (kept so nobody rediscovers them)

- `LogFormatMismatch` parses `found` in its constructor, and every call site passes a well-formed value.
- `newer_in(..., sep="_")` mishandles ill-formed names. Nothing creates one.
- `seal_sqlite` on a missing path would create a file. `migrate_one` `stat`s the path first.
- The payload `__post_init__`s enforce neither `claim_seq >= 1` nor the uniqueness of `honored`. A `claim_seq`
  below 1 never matches a claim, and a duplicate in `honored` is harmless to every fold.
- `_refuse_a_spent_id` reads every answer for the id (and every claimless `stopped` broadcast on the stop path)
  before breaking at `seq`. That happens once per `await_consumed` call, off the poll loop.
