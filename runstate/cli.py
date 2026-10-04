"""The ``runstate`` command: ``runstate migrate <root> [<rid> ...] [--to V]``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import formats
from .migrations import migrate
from .migrations.stores import PostgresStore, SqliteStore, Store

# The URI schemes libpq reads as a DSN (a key=value DSN is no directory either,
# which SqliteStore refuses).
_DSN_SCHEMES = ("postgres://", "postgresql://")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runstate")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate", help="move logs forward to a newer log format")
    m.add_argument("root", help="a directory (sqlite) or a DSN (postgres)")
    m.add_argument("run_ids", nargs="*", help="default: every run under the root")
    m.add_argument("--to", default=formats.LOG_FORMAT)
    m.add_argument("--backend", choices=["sqlite", "postgres"], default="sqlite")
    args = p.parse_args(argv)
    if args.backend == "sqlite" and args.root.startswith(_DSN_SCHEMES):
        p.error(
            "the root is a postgres DSN, not a sqlite root: run "
            f"`{formats.POSTGRES_MIGRATE_COMMAND}`"
        )
    store: Store = (
        PostgresStore(args.root)
        if args.backend == "postgres"
        else SqliteStore(Path(args.root))
    )
    for rid in migrate(store, args.run_ids or None, to=args.to):
        print(rid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
