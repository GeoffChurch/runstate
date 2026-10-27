"""The ``runstate`` command: ``runstate migrate <root> [<rid> ...] [--to V]``, with
``--assume``, ``--resolve`` and ``--accept`` for what a log leaves open
(log-formats.md §6, "Ambiguity")."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import formats, migrations
from .migrations import MigrationError, migrate
from .migrations.ambiguity import Limits, Undecided, parse_choices
from .migrations.stores import PostgresStore, SqliteStore, Store

# The URI schemes libpq reads as a DSN (a key=value DSN is no directory either,
# which SqliteStore refuses).
_DSN_SCHEMES = ("postgres://", "postgresql://")

# The states a step may hold at once on one run before it refuses the run, naming the
# record (the 0.3.0 -> 0.4.0 engine's cap). It decides a refusal, so it is set here,
# at the edge, and recorded with each migrated run. Memory grows as the cap times the
# records, since the engine keeps every layer's edges for its backward pass: about
# 420 bytes a state (664f8795: 4.3 million states over 4,017 records, peak 1.8 GB).
# 3,000 clears the widest layer measured on the real corpora (2,344, 664f8795) by a
# quarter, and refuses b02eb431 under facts too wide to follow at seq 81, in 6 s and
# 105 MB, where 20,000 exhausted 4 GB (the round's Task 21 report).
CAP = 3000

# The states a step may hold on one run summed over every layer, which memory follows:
# the cap bounds one layer, so a long log can hold a near-cap layer for thousands of
# records (about 16 GB at 3,000 over 13,000). 6,000,000 states is about 2.5 GB, clearing
# the most measured on the real corpora (664f8795's 4.3 million, 1.8 GB) by two fifths.
# The peak is the build's: the decode narrows the built graph in place of itself,
# holding the graph and the one its answer leaves, and its working set fits in what the
# build's per-layer worlds freed. Measured by RSS in separate processes (2026-10-27, the
# round's fix 4 report): 9,182 states, 66.7 MB built against 66.9 MB built and decoded;
# 20,694 states, 102.5 against 102.2 MB. Only a graph a few states wide, which is small
# in all, has a decode working set above its build's, by up to about 1.6 times the
# graph's own size.
BUDGET = 6_000_000

# How many labels past the log's highest step a run of passes that no record pins is
# followed, per claim (the 0.3.0 -> 0.4.0 engine's horizon). A limit, never a reading:
# a candidate that closes a pass at the horizon with its loop free to continue refuses
# the run, since how many passes it ran is then open. 2 follows the oracle, whose
# histories run on by at most one pass.
RUN_ON = 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runstate")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate", help="move logs forward to a newer log format")
    m.add_argument("root", help="a directory (sqlite) or a DSN (postgres)")
    m.add_argument("run_ids", nargs="*", help="default: every run under the root")
    m.add_argument("--to", default=formats.LOG_FORMAT)
    m.add_argument("--backend", choices=["sqlite", "postgres"], default="sqlite")
    m.add_argument(
        "--assume",
        action="append",
        default=[],
        metavar="MODULE:ATTR",
        help="facts a step's writers satisfy, as the object MODULE:ATTR names",
    )
    m.add_argument(
        "--resolve",
        action="append",
        default=[],
        metavar="PROJECTION=CHOOSER",
        help="a built-in chooser's name, or MODULE:ATTR, for one projection",
    )
    m.add_argument(
        "--accept",
        action="append",
        default=[],
        metavar="DIVERGENCE",
        help="migrate the runs where this divergence fires",
    )
    m.add_argument(
        "--cap",
        type=int,
        default=CAP,
        help=f"the states a step may hold at once on one run (default {CAP})",
    )
    m.add_argument(
        "--budget",
        type=int,
        default=BUDGET,
        help="the states a step may hold on one run summed over every layer "
        f"(default {BUDGET})",
    )
    m.add_argument(
        "--run-on",
        type=int,
        default=RUN_ON,
        help="labels past the log's steps an unpinned run of passes is followed, a "
        f"claim (default {RUN_ON})",
    )
    args = p.parse_args(argv)
    if args.backend == "sqlite" and args.root.startswith(_DSN_SCHEMES):
        p.error(
            "the root is a postgres DSN, not a sqlite root: run "
            f"`{formats.POSTGRES_MIGRATE_COMMAND}`"
        )
    try:
        choices = parse_choices(
            migrations.STEPS,
            assume=args.assume,
            resolve=args.resolve,
            accept=args.accept,
            limits=Limits(cap=args.cap, budget=args.budget, run_on=args.run_on),
        )
    except MigrationError as exc:
        p.error(str(exc))
    store: Store = (
        PostgresStore(args.root)
        if args.backend == "postgres"
        else SqliteStore(Path(args.root))
    )
    try:
        outcome = migrate(store, args.run_ids or None, to=args.to, choices=choices)
    except Undecided as exc:
        print(
            "runstate migrate: nothing was migrated. Each question below needs a "
            "--resolve or an --accept:\n",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        return 1
    for rid in outcome.moved:
        print(rid)
    for refusal in outcome.refused:
        print(f"refused: {refusal.message}", file=sys.stderr)
    return 1 if outcome.refused else 0


if __name__ == "__main__":
    sys.exit(main())
