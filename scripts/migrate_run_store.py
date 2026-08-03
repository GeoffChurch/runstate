#!/usr/bin/env python3
"""Bring existing sqlite run logs up to the current schema.

**Why this is a tool and not an implicit upgrade on attach.**
``SqliteChannel._open_existing`` deliberately creates and mutates *nothing* when
attaching: a stale pointer can resolve to a **foreign** valid sqlite db, and
attaching must not damage it (the PR #14 harm). Creating an index on attach is
precisely that forbidden mutation. So migration is an explicit operator act
against a store the operator names.

**Why a tool and not a permanent slow path.** The alternative — index new logs
only, leave old ones scanning — is a permanently bimodal read cost with no way
to tell the two apart from outside. Migrate; do not accommodate.

Idempotent: every statement in the schema is ``IF NOT EXISTS``, so re-running
changes nothing. It applies ``runstate.channel.sqlite._SCHEMA`` **verbatim**
rather than restating the DDL, so this tool cannot drift from the schema it
migrates to — a future index is picked up with no edit here.

Postgres needs no equivalent: ``ensure_schema`` runs at orchestration startup
and its ``CREATE INDEX IF NOT EXISTS`` already reaches every existing row.

    scripts/migrate_run_store.py --dry-run /path/to/runs
    scripts/migrate_run_store.py /path/to/runs [/another/store ...]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.request import pathname2url

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runstate.channel.sqlite import _SCHEMA  # noqa: E402

# The shape a runstate log has. A file that does not present exactly this is not
# ours to touch -- that is the whole guarantee _open_existing protects.
_EXPECTED_COLUMNS = {"seq", "topic", "name", "request_id", "body", "created_at"}


def _is_runstate_log(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='log'"
    ).fetchone()
    if row is None:
        return False
    cols = {r[1] for r in conn.execute("PRAGMA table_info(log)")}
    return cols == _EXPECTED_COLUMNS


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='log'"
            " AND name IS NOT NULL"
        )
    }


def _target_indexes() -> set[str]:
    """The index set a *fresh* log has — computed by applying the schema to a
    scratch in-memory db, so --dry-run never touches the real one."""
    scratch = sqlite3.connect(":memory:")
    try:
        scratch.executescript(_SCHEMA)
        return _indexes(scratch)
    finally:
        scratch.close()


def migrate(path: Path, *, dry_run: bool) -> str:
    """Apply the current schema to one log. Returns a one-line outcome."""
    # mode=rw + pathname2url mirrors _open_existing: never birth a file, and
    # treat ?/#/% in the path as literal filename characters, not URI syntax.
    uri = f"file:{pathname2url(str(path))}?mode={'ro' if dry_run else 'rw'}"
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as exc:
        return f"unreadable ({exc})"
    try:
        if not _is_runstate_log(conn):
            return "skipped (not a runstate log)"
        missing = _target_indexes() - _indexes(conn)
        if dry_run:
            return f"would add {sorted(missing)}" if missing else "up to date"
        if not missing:
            return "up to date"
        conn.executescript(_SCHEMA)
        still = _target_indexes() - _indexes(conn)
        if still:
            return f"FAILED (still missing {sorted(still)})"
        return f"added {sorted(missing)}"
    except sqlite3.Error as exc:
        return f"FAILED ({exc})"
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("roots", nargs="+", type=Path, metavar="ROOT")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change; mutate nothing",
    )
    args = ap.parse_args(argv)

    logs: list[Path] = []
    for root in args.roots:
        if root.is_file():
            logs.append(root)
        elif root.is_dir():
            logs.extend(sorted(root.glob("*.db")))
        else:
            print(f"no such path: {root}", file=sys.stderr)
            return 2

    if not logs:
        print("no .db files found", file=sys.stderr)
        return 1

    changed = failed = 0
    for log in logs:
        outcome = migrate(log, dry_run=args.dry_run)
        if outcome.startswith(("added", "would add")):
            changed += 1
        elif outcome.startswith("FAILED") or outcome.startswith("unreadable"):
            failed += 1
        print(f"{log}: {outcome}")

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{len(logs)} log(s), {changed} {verb}, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
