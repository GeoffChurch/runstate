# Header reads — a fold that needs only envelope fields still decodes every body

**Status: DEFERRED, measured.** Found 2026-11-02 while checking issue
[#66](https://github.com/GeoffChurch/runstate/issues/66), which it replaces. A reference-implementation change,
not a protocol one.

## The finding

#66 asked for an O(1) cold read of a run's commit count, because a viewer whose runs outnumber its fold pool
re-folds them cold every frame. Measured on 120 runs sampled from GeoffChurch/mycooc's 6,125 format-0.4.0
logs (median 3.6k records), a cold cockpit row costs ~54 ms (p90 ~100 ms), and the count is not where it goes:

| Fold, cold | Median |
|---|---|
| `LastElement` (the objective) | ~38 ms |
| `LiveDemand` | ~7 ms |
| `HeadFold` | ~5 ms |
| `PrefixLen(Commits())`, #66's count | ~5 ms |
| the unheld reads (`peek_terminal`, `last_activity`, `latest_episode`) | <1 ms |

So an O(1) count saves under a tenth of a cold row, and #66 is closed on that measurement. A commit that
carried its own count would also be redundant with the lineage, and a reader would have to walk the lineage to
check it.

`LastElement` is the cost because of what it must read. A mycooc run holds ~3,300 value records under 27 names,
~150 of them the objective's. The fold (`_NodeFold` in `runstate/observables.py`) reads every value record,
since it checks that each seq a node commits is a value and finds the objective's among them by name. It uses
each value's `seq` and `name`, both envelope fields, and never its body. But `read()` returns envelopes with
their bodies decoded, so it decodes all ~3,300. On 40 of those runs, reading the fold's topics through
`read()` took 30 ms (median); selecting only `seq`, `topic` and `name` for the same rows took 5.6 ms.

## The lever

Let a fold that needs only envelope fields read without decoding bodies. Estimated from the numbers above, a
cold row drops from ~54 ms to ~30 ms. That is a constant factor, not an order: the read is still O(log). Two
shapes:

- **Lazy bodies.** A backend's envelopes decode `body` on first access. No new operation, and every reader
  that skips bodies gains. But a byte-torn body then raises where it is first read rather than at `read()`,
  and runstate-tui's `corrupt` status relies on `read()` raising `json.JSONDecodeError`. `Envelope` is a frozen
  dataclass, so laziness also touches its equality.
- **An internal header read.** A backend method, beside the five operations and not part of the protocol, that
  returns `(seq, topic, name, request_id)` for the folds to use. Each backend implements it, and a fold reads
  the headers of value records and the full records of nodes.

## Revive when

A viewer must make cold reads faster after it has stopped repeating them. The cockpit's fix comes first, and
is the order-of-magnitude one: an ended run's row is fixed until its log grows, so it can be cached by
`last_seq` and never re-folded (GeoffChurch/runstate-tui `docs/backlog/fold-retention.md`). What remains after
that is each run's first cold read, once per cockpit session.
