"""Liveness handles (docs/design-v0.2.md §8).

A handle is a portable, scheme-tagged token an observer can resolve to a
liveness *fact* (alive / dead). The ``local`` scheme names a process on a host:

    local://{hostname}/{pid}

(A start-time / identity disambiguator for PID reuse is added when handle
*resolution* lands with the launcher convention; here the worker just
self-reports its handle.)
"""

from __future__ import annotations

import os
import socket


def local_handle() -> str:
    return f"local://{socket.gethostname()}/{os.getpid()}"


def handle_pid(handle: str) -> int | None:
    """The pid of a ``local://host/pid`` handle; None for a non-``local``
    scheme or an unparseable token. The ONE place the local-handle grammar is
    parsed — when the ``?start=T`` pid-reuse disambiguator lands
    (docs/backlog/conventions-hygiene.md F9), it lands here instead of
    breaking every consumer's rsplit."""
    if not handle.startswith("local://"):
        return None
    try:
        return int(handle.rsplit("/", 1)[1])
    except (ValueError, IndexError):
        return None


def resolve(handle: str) -> bool | None:
    """Liveness of a handle token, actor-independently. True/False for a
    ``local://host/pid`` (via ``os.kill(pid, 0)``); None if the scheme isn't
    locally resolvable (caller falls back to heartbeat staleness).

    Best-effort: the bare-string probe is heuristic (PID reuse; ``?start=T``
    disambiguator deferred — see docs/backlog/conventions-hygiene.md F9).
    Provable liveness comes from a held OS handle (spawner, via
    ``LaunchHandle.is_alive()``) or heartbeat-staleness (observer tier 4)."""
    pid = handle_pid(handle)
    if pid is None:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, not ours
