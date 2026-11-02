# Change notification — wait for a log to grow instead of polling it

**Status: DEFERRED, with a trigger.** Graduated 2026-11-02 from issue
[#16](https://github.com/GeoffChurch/runstate/issues/16), filed from the runstate-tui cockpit build, and closed
there. It absorbs the "Low-latency push" item that `../specs/channel-postgres.md` defers.

## The ask

The cockpit, and the headless daemon a multi-frontend cockpit would become, track live runs by polling a cheap
watermark: `last_seq()` per run per tick, then a read of the delta when it moves. #16 asks for a primitive that
lets them react instead, `subscribe(after)` or `wait_for_change()`: `LISTEN`/`NOTIFY` on Postgres, a watch on
the WAL file on SQLite.

## Why it is not built

- **It adds no expressive power** (the basis rubric's independence test). Polling `last_seq()`, O(1) on every
  backend, answers the same question. A notification lowers latency and the cost of idle runs, and nothing
  else, so it is an optimization over a basis that already spans the feature. Polling also stays the floor
  under it: a notification can be lost, so a waiter re-reads `last_seq()` after every wake and every timeout
  anyway.
- **No consumer pays enough for polling to feel it.** A warm cockpit row costs about 0.1 ms whatever the log's
  size (runstate-tui `docs/design.md`), and runstate's own waiters (`ensure`, which polls at a
  `poll_interval` its caller sets) are not latency-bound. The app-first rule (`../../CLAUDE.md`) builds against
  friction that exists. This is priority information, not a refutation; the trigger below is logical.
- **SQLite has no honest cross-process notification.** `sqlite3_update_hook` fires only in the connection that
  wrote. `PRAGMA data_version` sees other connections' writes but must itself be polled. inotify on the
  `-wal` file works on a local filesystem, not on the network filesystems a cluster's run homes sit on. A
  SQLite `wait` would be a poll hidden inside the backend.

## The shape, when built

- **An opt-in capability Protocol**, off the five-op base and detected with `isinstance` as `SessionProbe` is
  (`runstate/channel/base.py`): `wait(after: int, timeout: float) -> int`, returning `last_seq()` once it
  exceeds `after`, or when `timeout` passes. A caller resolves once, upstream, whether its channel offers it
  (a Strategy: the wait, or a poll at an interval the caller sets) and never asks again.
- **Postgres** offers it: a `LISTEN` on a dedicated connection, on a channel named by a bounded hash of the run
  id (identifiers stop at 63 bytes). The `NOTIFY` is sent after the append commits, never inside its
  transaction, because a `NOTIFY` in the transaction fails the commit when the server's notification queue is
  full, and a notification must never cost an append.
- **Memory** offers it with a condition variable.
- **SQLite** does not offer it, for the reason above.
- **Many runs at once** is not a channel operation, since a channel is one run. One listener multiplexed over
  every watched run is the viewer's job (`visualization-story.md`'s separate viewer project).

## Revive when

A consumer must react to many runs faster than it can afford to poll them. The concrete case is a cockpit
watching many runs on a remote Postgres, through the SSH tunnel mycooc's deployment plans. There, a per-run
`last_seq()` poll costs one round trip per run per frame: 500 ms a frame for 100 runs at a 5 ms round trip
(`claim-eviction.md` §10 measured the same arithmetic for one extra read).

Weigh a cheaper answer to that case first: one query returning every watched run's `last_seq` from Postgres's
shared `log` table. That is one round trip a frame without any notification, but it is a cross-run read, so
it is not a `Channel` operation either, and where it would live is part of the question.
