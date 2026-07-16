"""One-time migration for observer-clock (lifecycle-v0.4 + launcher-v0.4): give the
liveness records their now-required ``t`` (docs/specs/observer-clock.md §8).

TWO operations, deliberately different:

- ``lifecycle.started``: **RENAME** ``attached_at`` → ``t`` (value preserved). It is
  *not* stamped from ``created_at`` — a fabricated epoch is precision-load-bearing for
  ``history()``'s time replay, and stamping the row's wall-clock would silently shift the
  run epoch. A legacy **null** ``attached_at`` (the reference Worker never wrote one)
  falls back to the row's ``created_at``.
- ``lifecycle.heartbeat`` / ``lifecycle.stopped`` / ``launcher.launched`` /
  ``launcher.terminated``: **STAMP** ``t`` from the backend's existing ``created_at``
  column (freshness-only readers; ``created_at``'s pre-lock value is sound for an
  approximate age).

Quiescence-gated per db (a live episode → SKIP, re-run to converge — the migration is
complete only once no live episode remains) and idempotent (only rows whose body lacks
the target are touched). Orthogonal to the launcher-v0.3 ``request_id`` migration (a
different column, both already stamped or not). ``MemoryChannel`` has no persistence —
nothing to migrate.

Usage:  python scripts/migrate_observer_clock_v0_4.py ROOT [ROOT ...]
        (each ROOT is rglobbed for *.db files that are runstate logs)

Committed → run to convergence on the consumer roots → deleted (git carries it) — the
lifecycle-v0.3 / launcher-v0.3 precedent.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

from runstate.channel.sqlite import SqliteChannel
from runstate.observables import live_episode

STAMP_TOPICS = ("lifecycle.heartbeat", "lifecycle.stopped",
                "launcher.launched", "launcher.terminated")

# Belt beyond the quiescence gate: leave alone any log written to within this window
# (an extra guard for an actively-developed corpus). Recency is the log's OWN last-append
# time -- max(created_at) -- NOT the file mtime: under WAL the main .db mtime lags, and
# merely OPENING a db to read it touches the -wal/-shm mtimes to ~now (the
# wal-liveness-mtime.md trap), so an mtime-based check is self-defeating after any scan.
# created_at is the record's true append clock -- exactly what observer-clock surfaces.
RECENT_SECONDS = 3600.0


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _last_write(path: Path) -> float:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
            row = con.execute("SELECT max(created_at) FROM log").fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
    except (sqlite3.Error, TypeError, ValueError):
        return 0.0


def _recently_written(path: Path, within: float = RECENT_SECONDS) -> bool:
    return (time.time() - _last_write(path)) < within


def is_runstate_log(path: Path) -> bool:
    try:
        with sqlite3.connect(path) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(log)")}
    except sqlite3.Error:
        return False
    return {"seq", "topic", "body", "created_at"} <= cols


def migrate_db(path: Path) -> str:
    if live_episode(SqliteChannel(path)) is not None:
        return "skipped_live"
    with sqlite3.connect(path, timeout=10.0) as con:
        rows = con.execute(
            "SELECT seq, topic, body, created_at FROM log WHERE topic IN (?, ?, ?, ?, ?)",
            ("lifecycle.started", *STAMP_TOPICS),
        ).fetchall()
        dirty: list[tuple[str, int]] = []
        for seq, topic, body_text, created_at in rows:
            body = json.loads(body_text)
            if "t" in body:                       # already migrated / already new: idempotent
                continue
            if topic == "lifecycle.started":
                at = body.pop("attached_at", None)     # RENAME (value preserved)
                body["t"] = at if _is_number(at) else created_at   # legacy null -> created_at
            else:
                body["t"] = created_at                 # STAMP the freshness record
            dirty.append((json.dumps(body), seq))
        if not dirty:
            return "clean"
        con.executemany("UPDATE log SET body = ? WHERE seq = ?", dirty)
    return "migrated"


def main(roots: list[str]) -> None:
    counts = {"migrated": 0, "skipped_live": 0, "skipped_recent": 0, "clean": 0, "not_a_log": 0}
    for root in roots:
        for path in sorted(Path(root).rglob("*.db")):
            if not is_runstate_log(path):
                counts["not_a_log"] += 1
                continue
            if _recently_written(path):
                counts["skipped_recent"] += 1
                print(f"  recent (<1h), skipped: {path}")
                continue
            outcome = migrate_db(path)
            counts[outcome] += 1
            if outcome == "skipped_live":
                print(f"  live, skipped (re-run to converge): {path}")
    print(counts)
    if counts["skipped_live"]:
        sys.exit(1)  # not yet converged


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
