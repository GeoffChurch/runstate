"""One-time migration for the launcher-v0.3 bump: stamp the launch correlation
id (the envelope's ``request_id``) on the ``launcher.*`` records of existing
sqlite logs, and on the ``lifecycle.started`` that each launch's worker wrote.

The new fold (docs/specs/launcher-record-identity.md) attributes a death record
to the episode that CLAIMED its launch, by id. An id-less launcher record is
therefore unattributable — the verdict plane raises rather than guess — so old
logs must be stamped, not tolerated (there is no id-less fallback path, by
design: the fallback IS the forgery).

**Ids are minted here by positional pairing**, which is exactly what the old
fold assumed — but applied ONCE, offline, over a whole quiescent log rather than
live against a moving one, which is what made it wrong. Per db:

  - each ``launcher.launched`` mints ``mig-<seq>``;
  - a ``lifecycle.started`` takes the id of the newest launch not yet claimed
    (a launch is claimed at most once; a started with no launch above it is a
    HAND-RUN worker and stays id-less — correct: no launcher record speaks for
    its episode);
  - a ``launcher.terminated`` takes the id of the OLDEST launch not yet
    terminated (FIFO — which is what makes a late reap land on the episode it
    actually belongs to, the p2 case).

Concurrent launches in a historical log make the terminated pairing genuinely
ambiguous (that ambiguity is the bug being fixed; no offline rule can recover
what the writer never recorded). Such dbs are REPORTED by path — inspect them.

Quiescence-gated per db (a log with a live episode is SKIPPED — re-run to
converge) and idempotent (only NULL request_ids on these topics are touched).
The gate uses ``live_episode`` (handle probe), so run this on the host the
workers ran on.

Usage:  python scripts/migrate_launcher_v0_3.py ROOT [ROOT ...]
        (each ROOT is rglobbed for *.db files that are runstate logs)

Postgres analogue (none deployed outside CI as of 2026-07-14): the same walk
against ``UPDATE log SET request_id = ... WHERE run_id = ? AND seq = ?``.

Committed, run to convergence on the consumer roots, then DELETED (git carries
it) — the lifecycle-v0.3 precedent.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from runstate.channel.sqlite import SqliteChannel
from runstate.observables import live_episode

TOPICS = ("launcher.launched", "launcher.terminated", "lifecycle.started")


def is_runstate_log(path: Path) -> bool:
    try:
        with sqlite3.connect(path) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(log)")}
    except sqlite3.Error:
        return False
    return {"seq", "topic", "request_id", "body"} <= cols


def plan(rows: list[tuple[int, str, str | None]]) -> tuple[list[tuple[str, int]], bool]:
    """The (request_id, seq) stamps for one log, plus whether any two launches
    were ever open at once (the ambiguous, concurrent-launch history)."""
    stamps: list[tuple[str, int]] = []
    unclaimed: list[str] = []      # launches with no started yet (newest last)
    unterminated: list[str] = []   # launches with no terminated yet (oldest first)
    concurrent = False
    for seq, topic, request_id in rows:
        if request_id is not None:
            continue               # already stamped: idempotent
        if topic == "launcher.launched":
            launch = f"mig-{seq}"
            stamps.append((launch, seq))
            unclaimed.append(launch)
            unterminated.append(launch)
        elif topic == "lifecycle.started" and unclaimed:
            stamps.append((unclaimed.pop(), seq))          # newest unclaimed launch
        elif topic == "launcher.terminated" and unterminated:
            # >1 launch open at a death = the moment the writer's missing identity
            # becomes an actual guess (serial histories never reach it).
            concurrent = concurrent or len(unterminated) > 1
            stamps.append((unterminated.pop(0), seq))      # oldest open launch (FIFO)
    return stamps, concurrent


def migrate_db(path: Path) -> tuple[str, bool]:
    if live_episode(SqliteChannel(path)) is not None:
        return "skipped_live", False
    with sqlite3.connect(path, timeout=10.0) as con:
        rows = con.execute(
            "SELECT seq, topic, request_id FROM log WHERE topic IN (?, ?, ?) ORDER BY seq",
            TOPICS,
        ).fetchall()
        stamps, concurrent = plan(rows)
        if not stamps:
            return "clean", concurrent
        con.executemany("UPDATE log SET request_id = ? WHERE seq = ?", stamps)
    return "migrated", concurrent


def main(roots: list[str]) -> None:
    counts = {"migrated": 0, "skipped_live": 0, "clean": 0, "not_a_log": 0}
    ambiguous: list[Path] = []
    for root in roots:
        for path in sorted(Path(root).rglob("*.db")):
            if not is_runstate_log(path):
                counts["not_a_log"] += 1
                continue
            outcome, concurrent = migrate_db(path)
            counts[outcome] += 1
            if concurrent:
                ambiguous.append(path)
            if outcome == "skipped_live":
                print(f"  live, skipped (re-run to converge): {path}")
    for path in ambiguous:
        print(f"  CONCURRENT LAUNCHES -- terminated pairing is a guess: {path}")
    print(counts)
    if counts["skipped_live"]:
        sys.exit(1)  # not yet converged


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
