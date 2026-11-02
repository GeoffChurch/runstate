# Filtered reads — a window anchored in the past, under a filter

**Status: DEFERRED, with triggers.** Graduated 2026-11-02 from issue
[#15](https://github.com/GeoffChurch/runstate/issues/15), filed from the runstate-tui cockpit build, and closed
there.

## The ask, and what today's `read()` already answers

#15 asked `read()` for `filter=`, `before=`/`max_seq=`, windowing and search, each pushed into the backend's
query rather than applied by every consumer after the read. Three parts need nothing new:

- **Filters on the envelope's routing fields** exist: `topics` (exact or a `"prefix.>"` wildcard), `name` and
  `request_ids`, each in the backend's `WHERE`.
- **An unfiltered window anchored anywhere** is arithmetic. A log's seq is contiguous (`last_seq()` is the
  record count, `../design-v0.2.md` §4), so the N records ending before seq B are `read(after=B-N-1, limit=N)`.
  That is the drill-down's scrollback.
- **Search over `body` is out of scope.** The substrate never parses `body`. A body search is a derived,
  rebuildable index that a consumer builds (`../specs/store.md`, Recipe 5).

## What remains: three separate asks

1. **A filtered backward read: the last N records that match a filter, before seq B.** Records that match a
   filter are not contiguous in seq, so arithmetic cannot place the window. A client can page backward through
   unfiltered windows, filtering each, until N match; that costs every record it scans. The substrate, under
   an index-served filter (a topic, over the `(topic, seq)` index), costs N. The shape is a real choice: an
   upper bound `before=`, making the window the half-open interval (`after`, `before`), plus which end `limit`
   keeps (today the oldest; a backward read wants the newest), or a separate `last=N`. Either changes the base
   operation of every backend and of any other-language implementation.
2. **A richer predicate over envelope fields.** The cockpit's filter (runstate-tui `table.py`,
   `envelope_filter`) keeps a record when its topic or request id contains a substring, and drops the topic
   families the viewer has hidden, while a family it does not know always shows. Neither part is in `read()`'s
   vocabulary, which has no substring and no negation. These are envelope fields, so pushing them down would
   not parse `body`. But it grows the base operation toward a query language, and a substring match is not
   index-served, so the substrate would save the client a constant (decoding bodies it drops), not an order.
3. **A batched latest-per-topic: the newest record of each topic in a set, in one query.**
   `claim-eviction.md` §10 measured it on Postgres at 73.4 µs against 87.1 µs for two separate `latest()`
   calls, and the gap grows with the round trip.

## Why none is built

- No consumer makes a filtered backward read. The cockpit filters a bounded window of its newest 500 records,
  in memory, and its predicate is ask 2, which ask 1 would not carry anyway.
- Ask 2 moves query semantics into a primitive every implementation must reproduce, for a constant factor.
- Ask 3's motivation is a design not yet built (`claim-eviction.md`) on a deployment not yet made (the tunnel
  below).

This is priority information, not a refutation; the triggers below are logical.

## Revive when

- **Ask 1:** a consumer must show the last N records that match an index-served filter, in a log too long to
  page through on a keystroke. The cockpit's drill-down filtering a whole log, rather than its window, is the
  likely first.
- **Ask 2:** only alongside ask 1, and only for a predicate that is index-served. A substring match stays with
  the client.
- **Ask 3:** `claim-eviction.md` lands, or any reader polls several topics per run over a remote Postgres,
  such as the SSH tunnel mycooc's deployment plans, where each `latest()` costs a round trip.

The larger form of asks 1 and 2 is a query that is a value, which can be subtracted from and shipped to where
the data lives (`demand-driven-reads.md` §2). `change-notification.md` is the sibling ask from the same build.

## Downstream sites

runstate-tui marks the code that would delegate to a filtered read with `UPSTREAM(runstate filtered-reads)`:
`read_log_delta` in `table.py`, the drill-down's accumulation in `detail.py`, and a test lock in
`tests/test_detail.py`. When any ask here lands, grep for that marker there.
