"""The runstate substrate: a per-run, append-only **topic log** (v0.2).

A channel is one ordered, retained, multi-reader log of *envelopes*
``{seq, topic, name?, request_id?, body}``. The substrate routes/indexes on the
envelope and never parses ``body``. See docs/design-v0.2.md §4.

This module is the package *facade*: it imports the core record, the backends,
and the locator, and re-exports them. It is the top of the package's import DAG
— siblings import from ``.envelope`` (a leaf), never from here.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .envelope import Envelope
from .memory import MemoryChannel
from .sqlite import SqliteChannel

# In-process registry of MemoryChannel logs, keyed by (root, run_id). Lets
# several open_channel(..., backend="memory") calls in one process act as
# multiple readers/writers of the *same* run (the analogue of several
# SqliteChannels on one file). Each entry is (log, lock): the shared lock keeps
# the seq read-modify-write atomic across every instance on that log. Distinct
# (root, run_id) keys stay isolated.
_MEMORY_LOGS: dict = {}


def open_channel(run_id: str, *, root=None, backend: str = "sqlite", json_default=None):
    """Locate and open a run's channel.

    ``root`` is the directory (sqlite) or namespace (memory) holding runs;
    ``run_id`` selects one. Repeated calls on the same ``(root, run_id)`` share
    the run's log, so an orchestrator and a worker name the run the same way.
    ``json_default`` is a sender-side ``json.dumps`` hook for coercing exotic
    value payloads (e.g. numpy scalars -> float); readers are unaffected.
    """
    if backend == "sqlite":
        return SqliteChannel(Path(root) / f"{run_id}.db", json_default=json_default)
    if backend == "memory":
        log, lock = _MEMORY_LOGS.setdefault((str(root), run_id), ([], threading.Lock()))
        return MemoryChannel(log, lock, json_default=json_default)
    raise ValueError(f"unknown backend: {backend!r} (expected 'sqlite' or 'memory')")


__all__ = ["Envelope", "MemoryChannel", "SqliteChannel", "open_channel"]
