# Liveness/freshness must not be read from the main-`.db` mtime under WAL

**Status:** caveat surfaced 2026-06-23 from a consumer (mycooc `run_experiment.py --status`). Filed here
because runstate is the shared sqlite-log substrate and defaults to `journal_mode=WAL` (see
`exogenous-commit-audit.md`, commit `3304e8c`). Disposition OPEN — **SUBSUMED 2026-07-14 by
[third-party-observer](third-party-observer.md) item 1, which also RE-GRADES it.**

**The grade below ("minor · observability") was wrong.** It is minor only for the persona in view
when it was filed — an orchestrator watching its own live run, for whom a bad clock is a cosmetic
sawtooth. For a *third party attaching to a run it did not launch*, the same missing clock is a
**wrong verdict**: a run dead 21 days reports `Running(beacon_age=9.5e-06)`, because no liveness
record carries a time and the Watcher's staleness clock seeds at registration. The consumer-side fix
below (sidecar mtimes / `max(created_at)`) stands, and the runstate-side fix it proposes — a
`freshness()` helper — is option (c) of the ledger's design fork. Severity is persona-relative.

---

## L1 — [minor · observability] file-mtime freshness is stale under WAL

**Where:** any consumer that derives "time since last activity / liveness / a live pulse" from the
channel db's **main `.db` file mtime** (`Path(db).stat().st_mtime`). It surfaced in mycooc's `--status`
"Latest run … [N s ago]" line, but the trap is generic to anything built on a runstate sqlite channel.

**Defect:** under WAL, per-write activity lands in the `-wal` sidecar; the main `.db` file's mtime only
advances on a **checkpoint** (periodic, not per-commit). So the main-db mtime lags real writes by up to
a checkpoint interval, and any derived "[N s ago]" **sawtooths** — it grows between checkpoints *even
while the run is actively writing every step*, then snaps back when a checkpoint fires.

**Verified:** on a live, healthy run — main `.db` mtime **306 s** old while `-wal`/`-shm` mtime was
**1 s** old and the per-step value events were ~28 s apart. So the mtime-based age read "stale" on a
perfectly live run (a 222 s lag vs the actual last event).

**Impact:** a "live pulse" or stall indicator built on the main-db mtime is misleading — it can read
stale on a healthy run and becomes ~redundant with the coarser between-dispatch status register. A
timestamp/stall watchdog keyed on it would false-fire or miss real stalls.

**Candidate fix (consumer-side):** compute freshness from a source that tracks every commit — either
`max(mtime(.db), mtime(.db-wal), mtime(.db-shm))`, or better, the **last `created_at` in the log table**
(the actual event time). mycooc adopted the sidecar-max fix in `run_experiment.py`.

**Candidate fix (runstate-side, optional):** expose a first-class `last_write_ts` / freshness helper (or
document it in `overview.md`) so consumers don't reach for the wrong file mtime. Given WAL is the
default and `Heartbeat`/`live_*` already exist, a `freshness()` that reads max(created_at) (or the
sidecar mtimes) would make the right thing the easy thing.

**Confidence:** high — root cause confirmed (journal_mode=wal + 222 s main-db mtime lag while the -wal
mtime + log events were current).
