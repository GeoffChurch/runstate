"""The runstate substrate: a per-run, append-only **topic log** (v0.2).

A channel is one ordered, retained, multi-reader log of *envelopes*
``{seq, topic, name?, request_id?, body}``. The substrate routes/indexes on the
envelope and never parses ``body``.

``send(expected_seq=)`` is the substrate's **compare-and-append**: the check and
the append are one critical section across handles and processes. ``None`` means
the claim was provably lost (the log moved); a raise means the outcome was
indeterminate (a backend fault, e.g. a competing writer wedged past the wait
bound) — never a loss. Every backend must honor this; the conformance race
tests in ``tests/test_channel.py`` pin it. See docs/design-v0.2.md §4.

This module is the package *facade*: it imports the core record, the backends
and the ``Logs`` Protocols, and re-exports them. The places that open channels
(``SqliteLogs``, ``PostgresLogs``, ``MemoryLogs``) are ``runstate.logs``'s. It is
the top of the package's import DAG — siblings import from ``.envelope`` (a
leaf), never from here.
"""

from __future__ import annotations

from pathlib import Path

from .. import formats
from ..formats import LOG_FORMAT, FORMATS, LogFormatMismatch, LogFormatMissing
from .base import (
    Channel,
    Logs,
    ProcessLogs,
    RunNotFound,
    Session,
    SessionProbe,
)
from .envelope import Body, Envelope
from .memory import MemoryChannel, MemoryLog
from .sqlite import SqliteChannel


def sqlite_log_path(root: Path, run_id: str) -> Path:
    """The run's address at the current format, once log-formats.md §4's opening
    checks pass, in order: refuse a root holding a newer format; the current
    address, if the run's log is there; refuse an older format or the legacy
    address holding the run; else the current address, which holds nothing.
    ``SqliteLogs`` opens or births a channel there, and a run copy reads or writes
    rows there (docs/specs/run-copy.md)."""
    formats.check_sqlite_names(root, run_id)
    if root.is_dir():
        newer = formats.newer_in(
            (p.name for p in root.iterdir() if p.is_dir()),
            prefix="v",
            sep=".",
            than=LOG_FORMAT,
        )
        if newer:
            raise LogFormatMismatch(
                found=newer[-1],
                expected=LOG_FORMAT,
                where=str(root),
                command=formats.sqlite_migrate_command(root),
            )
    path = FORMATS[LOG_FORMAT].sqlite_path(root, run_id)
    if path.exists():
        return path
    for version, layout in formats.older_than(LOG_FORMAT):
        old = layout.sqlite_path(root, run_id)
        if old.exists():
            raise LogFormatMismatch(
                found=version,
                expected=LOG_FORMAT,
                where=str(old),
                command=formats.sqlite_migrate_command(root),
            )
    legacy = formats.legacy_sqlite_path(root, run_id)
    if legacy.exists():
        raise LogFormatMissing(
            where=str(legacy), instructions=formats.sqlite_onboarding(root, run_id)
        )
    return path


__all__ = [
    "Body",
    "Channel",
    "Envelope",
    "Logs",
    "MemoryChannel",
    "MemoryLog",
    "ProcessLogs",
    "RunNotFound",
    "Session",
    "SessionProbe",
    "SqliteChannel",
    "sqlite_log_path",
]
