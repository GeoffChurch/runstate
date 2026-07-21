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

This module is the package *facade*: it imports the core record, the backends,
and the locator, and re-exports them. It is the top of the package's import DAG
— siblings import from ``.envelope`` (a leaf), never from here.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path

from .base import Channel, EpisodeHolder, EpisodeProbe, RunNotFound
from .envelope import Body, Envelope
from .memory import MemoryChannel
from .sqlite import SqliteChannel

# In-process registry of MemoryChannel logs, keyed by (root, run_id). Lets
# several ``create_channel`` / ``attach_channel`` (..., backend="memory") calls
# in one process act as multiple readers/writers of the *same* run (the analogue
# of several SqliteChannels on one file). Each entry is (log, lock): the shared
# lock keeps the seq read-modify-write atomic across every instance on that log.
# Distinct (root, run_id) keys stay isolated; the root is identity-normalized
# (absolute path, None kept as None) so two spellings of one location share a log
# exactly as they would share a file on sqlite -- and root=None never collides
# with a namespace literally named "None".
_MEMORY_LOGS: dict[tuple[str | None, str], tuple[list[Envelope], threading.Lock]] = {}


def _locate(
    run_id: str,
    *,
    root: str | os.PathLike[str] | None = None,
    backend: str = "sqlite",
    create: bool,
    json_default: Callable[[object], object] | None = None,
) -> Channel:
    """The backend-dispatch ladder behind the two public locators.

    ``create`` forks the two contracts and lives ONLY here — the public basis is
    two total functions (``create_channel`` / ``attach_channel``), each with one
    contract, never a boolean flag. ``create=True`` is the birth path (open-or-
    create, idempotent via the birth-CAS); ``create=False`` attaches to an
    existing run and raises ``RunNotFound`` if it has no records, mutating no
    backing store (docs/specs/channel-locators.md).
    """
    if backend == "sqlite":
        if root is None:
            raise ValueError(
                "the sqlite backend requires a root directory (got root=None)"
            )
        return SqliteChannel(
            Path(root) / f"{run_id}.db", create=create, json_default=json_default
        )
    if backend == "memory":
        key = (None if root is None else os.path.abspath(str(root)), run_id)
        if create:
            log, lock = _MEMORY_LOGS.setdefault(key, ([], threading.Lock()))
        else:
            # Attach: look up WITHOUT setdefault (no phantom); an absent key or an
            # empty log is "no records" -> the run does not exist.
            entry = _MEMORY_LOGS.get(key)
            if entry is None or not entry[0]:
                raise RunNotFound(
                    f"run {run_id!r} has no records (memory backend, root={root!r})"
                )
            log, lock = entry
        return MemoryChannel(log, lock, json_default=json_default)
    if backend == "postgres":
        if root is None:
            raise ValueError("the postgres backend requires a DSN root (got root=None)")
        try:
            from .postgres import PostgresChannel
        except ImportError as exc:  # the optional extra isn't installed
            raise ImportError(
                "the postgres backend needs psycopg: pip install runstate[postgres]"
            ) from exc
        return PostgresChannel(
            str(root), run_id, create=create, json_default=json_default
        )
    raise ValueError(
        f"unknown backend: {backend!r} (expected 'sqlite', 'memory', or 'postgres')"
    )


def attach_channel(
    run_id: str,
    *,
    root: str | os.PathLike[str] | None = None,
    backend: str = "sqlite",
    json_default: Callable[[object], object] | None = None,
) -> Channel:
    """Attach to an **existing** run's channel; raise ``RunNotFound`` if it has
    no records. Never creates or mutates any backing store (the foreign-db-safe
    open) — yet **writable**: an attached handle reads *and* writes, so the stop
    path attaches then sends. The non-creating half of the locator split; birth
    is always the explicitly-named ``create_channel``.

    ``root`` is the directory (sqlite) or namespace (memory) holding runs;
    ``run_id`` selects one. Repeated calls on the same ``(root, run_id)`` share
    the run's log. ``json_default`` is a sender-side ``json.dumps`` hook for
    coercing exotic value payloads (e.g. numpy scalars -> float); readers are
    unaffected. (docs/specs/channel-locators.md)
    """
    return _locate(
        run_id, root=root, backend=backend, create=False, json_default=json_default
    )


def create_channel(
    run_id: str,
    *,
    root: str | os.PathLike[str] | None = None,
    backend: str = "sqlite",
    json_default: Callable[[object], object] | None = None,
) -> Channel:
    """Open-or-create (birth) a run's channel; idempotent via the birth-CAS. The
    one side-effecting locator — creation is *always* this explicitly-named call,
    never a default of attaching.

    ``root`` is the directory (sqlite) or namespace (memory) holding runs;
    ``run_id`` selects one. Repeated calls on the same ``(root, run_id)`` share
    the run's log, so an orchestrator and a worker name the run the same way.
    ``json_default`` is a sender-side ``json.dumps`` hook for coercing exotic
    value payloads (e.g. numpy scalars -> float); readers are unaffected.
    """
    return _locate(
        run_id, root=root, backend=backend, create=True, json_default=json_default
    )


__all__ = [
    "Body",
    "Channel",
    "EpisodeHolder",
    "EpisodeProbe",
    "Envelope",
    "MemoryChannel",
    "RunNotFound",
    "SqliteChannel",
    "attach_channel",
    "create_channel",
]
