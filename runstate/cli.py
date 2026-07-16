"""runstate CLI — a deliberately small terminal tool over a run's topic log.

Two commands, both reading (and for ``stop``, appending to) a per-run sqlite log
directly:

    runstate status <root>              # a snapshot table of the runs under <root>
    runstate stop <root> <run_id>       # send a cooperative control.stop [--wait N]

What this deliberately is **NOT**: a daemon, a live viewer, or a data-plane
surface. It renders one static snapshot and exits — it never tails, never loops
(beyond ``--wait``'s single bounded block). The live viewer and the richer
data-plane story are a **separate project** (docs/backlog/webapp-viewer.md,
docs/backlog/visualization-story.md), not this.

**sqlite only.** Postgres discovery has no shape yet — every Postgres op is scoped
``WHERE run_id`` with no roots or directories to enumerate
(docs/backlog/third-party-observer.md item 3) — so the CLI does not attempt it.

A tool, not API: ``main`` is the console-script entry point (``runstate = ...``),
and nothing here is re-exported from ``runstate.__all__``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from .channel import Channel, SqliteChannel
from .observables import (
    MalformedRecordError,
    RunResult,
    last_activity,
    latest_episode,
    live_episode,
    peek_terminal,
    progress,
)
from .vocabulary.payloads import Nak, Topic
from .watcher import await_consumed


def _is_runstate_log(path: Path) -> bool:
    """True iff ``path`` is an existing sqlite file with a runstate ``log`` table.
    A pure READ-ONLY probe (``mode=ro``): it never creates the file and never adds
    a schema, so discovery cannot phantom-create a db (third-party-observer item 4)
    nor pollute a foreign ``.db`` that happens to sit under the root."""
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(log)")}
    except sqlite3.Error:
        return False
    return {"seq", "topic", "body"} <= cols


def _discover(root: Path) -> list[tuple[str, Path]]:
    """``(run_id, db_path)`` for every runstate log under ``root``, in BOTH
    layouts: flat ``<root>/<rid>.db`` and the Recipe-1 sharded
    ``<root>/runs/<xx>/<rid>/<rid>.db`` (specs/store.md Recipe 1). Paths are
    globbed (statted), never constructed-then-opened, and each is confirmed a
    runstate log before it is reported — so the CLI only ever opens files that
    already exist and are ours. ``run_id`` is the file stem."""
    seen: dict[str, Path] = {}
    for pattern in ("*.db", "runs/*/*/*.db"):
        for p in sorted(root.glob(pattern)):
            if p.stem not in seen and _is_runstate_log(p):
                seen[p.stem] = p
    return sorted(seen.items())


def _resolve_db(root: Path, run_id: str) -> Optional[Path]:
    """The existing runstate-log path for ``run_id`` under ``root`` (flat or
    Recipe-1 sharded), or None — checked by ``exists()`` + the read-only probe, so
    a missing run never fabricates a db (third-party-observer item 4)."""
    candidates = (
        root / f"{run_id}.db",
        root / "runs" / run_id[:2] / run_id / f"{run_id}.db",
    )
    for path in candidates:
        if path.exists() and _is_runstate_log(path):
            return path
    return None


def _verdict(ch: Channel) -> str:
    """The run's status cell: the terminal ``outcome`` if a terminal record exists,
    else ``live`` / ``idle`` / ``never-started`` (the stateless reads a snapshot can
    do; the staleness inference that would turn a stale ``idle`` into presumed-dead
    is the Watcher's, and needs clock state the CLI does not carry)."""
    terminal = peek_terminal(ch)
    if terminal is not None:
        return str(terminal.outcome)
    if live_episode(ch) is not None:
        return "live"
    if latest_episode(ch) is not None:
        return "idle"  # started, not live, no terminal record (crashed-recordless / foreign host)
    return "never-started"


def _row(run_id: str, path: Path) -> tuple[str, str, str, str]:
    """One status row: ``(run_id, verdict, progress, age)``. A malformed verdict
    record is caught per-run and rendered as a cell (never crashes the table); the
    measurement folds (``progress`` / ``last_activity``) skip junk and do not raise.
    Opens → reads → closes one handle, so open fds stay bounded across many runs."""
    with SqliteChannel(path) as ch:
        try:
            verdict = _verdict(ch)
        except MalformedRecordError as exc:
            verdict = f"malformed ({exc.detail})"
        prog = progress(ch)
        activity = last_activity(ch)
    prog_cell = "-" if prog is None else str(prog)
    age_cell = "-" if activity is None else f"{time.time() - activity:.0f}s"
    return run_id, verdict, prog_cell, age_cell


def _cmd_status(root: Path) -> int:
    runs = _discover(root)
    if not runs:
        print(f"no runs under {root}")
        return 0
    rows = [_row(rid, path) for rid, path in runs]
    w_id = max(len("run_id"), *(len(r[0]) for r in rows))
    w_verdict = max(len("verdict"), *(len(r[1]) for r in rows))
    w_prog = max(len("progress"), *(len(r[2]) for r in rows))
    print(f"{'run_id':<{w_id}}  {'verdict':<{w_verdict}}  {'progress':<{w_prog}}  age")
    for run_id, verdict, prog_cell, age_cell in rows:
        print(
            f"{run_id:<{w_id}}  {verdict:<{w_verdict}}  {prog_cell:<{w_prog}}  {age_cell}"
        )
    # age is now() - the newest record's own t: a CROSS-CLOCK estimate (the record's
    # clock is the worker's), display-only -- never an ordering key (observer-clock §4).
    print("(age = now() - last activity; a cross-clock estimate, display-only)")
    return 0


def _cmd_stop(root: Path, run_id: str, wait: Optional[float]) -> int:
    path = _resolve_db(root, run_id)
    if path is None:
        print(
            f"no run {run_id!r} under {root} (looked for a flat <rid>.db and a "
            f"sharded runs/<xx>/<rid>/<rid>.db); refusing to create one",
            file=sys.stderr,
        )
        return 1
    request_id = f"cli:{uuid.uuid4().hex}"
    with SqliteChannel(path) as ch:
        try:
            down = live_episode(ch) is None
        except MalformedRecordError:
            down = True  # can't confirm a live episode -> treat as down, and warn
        seq = ch.send({}, topic=Topic.CONTROL_STOP, request_id=request_id)
        assert (
            seq is not None
        )  # an unconditional append (no expected_seq) always returns a seq
        print(f"sent control.stop to {run_id} (request_id={request_id}, seq={seq})")
        if down:
            # design §7 S2: a stop sent while the run is down is honored by the NEXT
            # episode, exactly once. Defensible for a caller who knows the run is
            # down; a landmine for one who thinks it is live (third-party-observer
            # item 6) -- so say so plainly.
            print(
                "  warning: no live episode -- this stop is ARMED for the NEXT "
                "episode and honored exactly once when a worker next attaches. If "
                "the run is finished it will halt that resume at step 0."
            )
        if wait is not None:
            try:
                answer = await_consumed(ch, seq, request_id=request_id, timeout=wait)
            except TimeoutError:
                print(
                    f"  --wait: not consumed within {wait}s (not a refusal -- the "
                    f"worker may be busy, or down with the stop armed)"
                )
                return 0
            if answer is None:
                print("  --wait: accepted (consumed at the heartbeat watermark)")
            elif isinstance(answer, Nak):
                print(f"  --wait: refused (nak {answer.reason}): {answer.message}")
            elif isinstance(answer, RunResult):
                print(
                    f"  --wait: refused-by-death (the run went terminal: "
                    f"{answer.outcome})"
                )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runstate",
        description="Minimal control-plane CLI over a run's sqlite topic log "
        "(sqlite only; not a daemon or viewer -- see the module docstring).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="snapshot table of the runs under a root")
    p_status.add_argument("root", type=Path, help="directory holding the run logs")

    p_stop = sub.add_parser("stop", help="send a cooperative control.stop to a run")
    p_stop.add_argument("root", type=Path, help="directory holding the run logs")
    p_stop.add_argument("run_id", help="the run to stop")
    p_stop.add_argument(
        "--wait",
        type=float,
        default=None,
        metavar="SECONDS",
        help="block up to SECONDS for the stop to be answered (accepted / nak / "
        "refused-by-death / timeout)",
    )

    args = parser.parse_args(argv)
    if args.cmd == "status":
        return _cmd_status(args.root)
    if args.cmd == "stop":
        return _cmd_stop(args.root, args.run_id, args.wait)
    return 2  # unreachable: subparsers are required


if __name__ == "__main__":
    raise SystemExit(main())
