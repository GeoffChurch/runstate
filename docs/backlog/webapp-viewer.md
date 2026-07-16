# webapp-viewer — live monitoring UI for active runs

*(Rewritten 2026-07-16 to the v0.2 topic-log surface — the original v0.1 sketch
(`messages` table, `direction='to_orchestrator'`, roles, `control.send_stop`,
`iter_history()`) is in git history. This entry is the minimal single-root
tool; the full data-plane / viewer-protocol story is a **separate project** —
see [visualization-story](visualization-story.md).)*

A standalone webapp that lists the runs under a root, tails their progress in
real time, and provides a per-run cooperative stop button. Equivalent in
spirit to mycooc's `--status` table, but as a browser UI that auto-updates.

## What it provides

- **Discovery** — enumerate the root's channels (flat `<root>/<run_id>.db`, or
  `runs/<rid[:2]>/<rid>/` under content-addressed placement,
  `../specs/store.md` Recipe 1). Real trees are mixed (~25% of mycooc's cells
  are Recipe-1; translation is flat — `third-party-observer.md` item 3), so
  discovery is a pluggable app-side resolver, never a runstate opinion. Stat
  before opening: `open_channel` on a nonexistent rid *creates* it (item 4's
  read-only-open gap).
- **Status row per run** — the observables: `peek_terminal` / `live_episode`
  (verdict + liveness), `progress` (the step frontier), `latest("value", name)`
  for register-style metrics. Measured ~54 µs/run warm (visualization-story),
  so a 1 Hz table over ~1,200 runs costs ~64 ms/frame — budget it, and pool
  channels behind an LRU (a `SqliteChannel` holds 3 fds; a naive viewer EMFILEs
  at ~340 open runs on a 1024-fd default).
- **Live tail** — per-run caller-owned cursor: poll `last_seq()` as the
  has-anything-new watermark (the §4-sanctioned consumer), `read(after=cursor)`
  for the delta, push to the browser via SSE/WebSocket, and fold plot state
  incrementally — a full `value_series` refold per frame is not viable at 10⁶
  envelopes (~1.9 s, ~0.77 GB transient).
- **Stop button** — send `{}` on `topic="control.stop"` with a
  `request_id="webui:<unique>"` (the §12.8 attribution stopgap); confirm via
  `await_consumed` / `observables.undischarged_stops`. Works even if the
  original launcher is gone — multiple orchestrators are supported by the
  drain model (§12.7). Caveat (`third-party-observer.md` item 6): a stop sent
  to a dead-but-reads-live run arms for the *next* episode.

## Constraints

- **sqlite / postgres backends only** (a memory log is process-local). All
  reads go through the public Channel surface — no reaching into the `log`
  table (the v0.1 sketch's direct-SQL reads predate the observables).
- **Freshness needs the observer clock.** `Watcher.poll`'s `beacon_age` is
  arrival-relative and lies to a late-attaching viewer (a run dead 21 days
  reads `Running`); the honest freshness column waits on
  [`../specs/observer-clock.md`](../specs/observer-clock.md) (`t` on the
  beacon + the `last_activity` fold).

## What it requires from the library

The tail itself needs nothing new (`last_seq()` + `read(after=)` are the
sanctioned incremental-reader pattern). The real prerequisites are the
third-party-observer ledger's items: **1** the observer clock (CONVERGED —
`../specs/observer-clock.md`), **3** run enumeration, **4** read-only open,
**5** cursored/public value folds (`_value_points` is the designated escape
hatch awaiting promotion).

## Status

Deferred — blocked on the third-party-observer items above rather than on
viewer code; FastAPI + SSE (or WebSockets) plus a vanilla-JS frontend remains
the implementation sketch. Ships as a separate package that imports runstate.
