"""One-time migration for the observer-clock bump (lifecycle-v0.4 + launcher-v0.4,
docs/specs/observer-clock.md): stamp the required ``t`` onto the dated convention
bodies of existing sqlite logs, from the backend's own ``created_at`` column.

The bump makes ``t`` REQUIRED (non-null) on ``lifecycle.heartbeat`` /
``lifecycle.stopped`` / ``launcher.launched`` / ``launcher.terminated`` and RENAMES
``lifecycle.started.attached_at`` -> ``t`` (also required non-null). Per the
no-compat doctrine an old body is not tolerated -- the verdict folds raise and
``Heartbeat(**body)`` TypeErrors on the v0.3 shape -- so old logs are stamped, not
read leniently. Every row already carries ``created_at`` (the substrate writes it
on append; verified 1,998/1,998 on a sampled log), which is exactly the emit-time
``t`` should have been, so the stamp is faithful, not fabricated.

Per db:
  - ``lifecycle.heartbeat`` / ``lifecycle.stopped`` / ``launcher.launched`` /
    ``launcher.terminated``: add ``t = created_at`` iff the body has no ``t`` key;
  - ``lifecycle.started``: rename ``attached_at`` -> ``t``, PREFERRING a non-null
    ``attached_at`` value (that WAS the emit time) and falling back to
    ``created_at`` when it is absent or null.

Quiescence-gated per db (a log with a live episode is SKIPPED -- re-run to
converge; the half-migrated world is benign at runtime: a v0.3 beacon simply reads
as junk to the Watcher's seed, i.e. now(), the pre-clock behavior). The gate uses
``live_episode`` (handle probe), so run this on the host the workers ran on; an
unresolvable foreign handle reads conservatively live and the db stays skipped.
Idempotent -- keyed on the key's presence (a ``t`` already set, ``attached_at``
already gone) is left untouched, so re-running converges without double-stamping.

Usage:  python scripts/migrate_observer_clock_v0_4.py ROOT [ROOT ...]
        (each ROOT is rglobbed for *.db files that are runstate logs)

Postgres analogue (none deployed outside CI as of 2026-07-16; not implemented here
because the per-run quiescence gate + shared-table scoping don't stay a one-liner):
  UPDATE log SET body = jsonb_set(body::jsonb, '{t}', to_jsonb(created_at))::text
  WHERE topic IN ('lifecycle.heartbeat','lifecycle.stopped',
                  'launcher.launched','launcher.terminated')
    AND NOT (body::jsonb ? 't') AND run_id = %s;   -- plus the started rename
run per quiescent run_id (live_episode is None), the same walk as below.

Per the observer-clock spec this script is committed, run to convergence on the
consumer roots, then DELETED (git carries it) -- the lifecycle-v0.3 / launcher-v0.3
precedent. Run it ONLY against quiescent logs you are authorized to migrate.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from runstate.channel.sqlite import SqliteChannel
from runstate.observables import live_episode

DATED_TOPICS = (
    "lifecycle.heartbeat",
    "lifecycle.stopped",
    "launcher.launched",
    "launcher.terminated",
)
TOPICS = ("lifecycle.started", *DATED_TOPICS)


def is_runstate_log(path: Path) -> bool:
    try:
        with sqlite3.connect(path) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(log)")}
    except sqlite3.Error:
        return False
    return {"seq", "topic", "body", "created_at"} <= cols


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _restamp(topic: str, body: dict, created_at: float) -> bool:
    """Stamp ``t`` on one body in place (from created_at / a kept attached_at).
    Returns True iff the body changed (idempotent: keyed on the key's presence)."""
    if topic == "lifecycle.started":
        if "attached_at" in body:
            at = body.pop("attached_at")
            if "t" not in body:  # prefer the real emit time; else created_at
                body["t"] = at if _is_num(at) else created_at
            return True  # popping attached_at is itself a change
        if "t" not in body:
            body["t"] = created_at
            return True
        return False
    # the four dated topics: add t iff absent
    if "t" not in body:
        body["t"] = created_at
        return True
    return False


def migrate_db(path: Path) -> str:
    if live_episode(SqliteChannel(path)) is not None:
        return "skipped_live"
    with sqlite3.connect(path, timeout=10.0) as con:
        rows = con.execute(
            "SELECT seq, topic, body, created_at FROM log WHERE topic IN "
            "(?, ?, ?, ?, ?) ORDER BY seq",
            TOPICS,
        ).fetchall()
        dirty = []
        for seq, topic, body_text, created_at in rows:
            body = json.loads(body_text)
            if not isinstance(body, dict):
                continue  # a foreign non-object body on a reserved topic: leave it
            if _restamp(topic, body, float(created_at)):
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
