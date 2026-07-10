"""One-time migration for the lifecycle-v0.3 bump: strip the dead ``hostname``
key from ``lifecycle.started`` bodies in existing sqlite logs.

Quiescence-gated per db (a log with a live episode is SKIPPED — re-run to
converge; the half-migrated world is benign at runtime, nothing strict-parses
Started off logs) and idempotent (only rows whose body carries the key are
touched). The gate uses ``live_episode`` (handle probe), so run this on the
host the workers ran on; an unresolvable foreign handle reads conservatively
live and the db stays skipped.

Usage:  python scripts/migrate_lifecycle_v0_3.py ROOT [ROOT ...]
        (each ROOT is rglobbed for *.db files that are runstate logs)

Postgres analogue (none deployed outside CI as of 2026-07-10):
  UPDATE log SET body = body::jsonb - 'hostname'  -- via a text cast round-trip
  WHERE topic = 'lifecycle.started' AND body::jsonb ? 'hostname';

Per the 2026-07-10 ruling this script is committed, run to convergence on the
two consumer repos' roots, then DELETED (git carries it).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from runstate.channel.sqlite import SqliteChannel
from runstate.observables import live_episode


def is_runstate_log(path: Path) -> bool:
    try:
        with sqlite3.connect(path) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(log)")}
    except sqlite3.Error:
        return False
    return {"seq", "topic", "body"} <= cols


def migrate_db(path: Path) -> str:
    if live_episode(SqliteChannel(path)) is not None:
        return "skipped_live"
    with sqlite3.connect(path, timeout=10.0) as con:
        rows = con.execute(
            "SELECT seq, body FROM log WHERE topic = 'lifecycle.started'"
        ).fetchall()
        dirty = []
        for seq, body_text in rows:
            body = json.loads(body_text)
            if "hostname" in body:
                body.pop("hostname")
                dirty.append((json.dumps(body), seq))
        if not dirty:
            return "clean"
        con.executemany("UPDATE log SET body = ? WHERE seq = ?", dirty)
    return "migrated"


def main(roots: list[str]) -> None:
    counts = {"migrated": 0, "skipped_live": 0, "clean": 0, "not_a_log": 0}
    for root in roots:
        for path in sorted(Path(root).rglob("*.db")):
            if not is_runstate_log(path):
                counts["not_a_log"] += 1
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
