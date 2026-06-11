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


def _parse_local(handle):
    """``(host, pid)`` of a ``local://host/pid`` token, or None — THE one
    parse site for the local-handle grammar (audit F8; the ``?start=T``
    disambiguator of conventions-hygiene F9 lands here)."""
    if not isinstance(handle, str) or not handle.startswith("local://"):
        return None
    host, sep, pid_s = handle[len("local://"):].rpartition("/")
    if not sep:
        return None
    try:
        return host, int(pid_s)
    except ValueError:
        return None


def handle_pid(handle: str) -> int | None:
    """The pid of a ``local://host/pid`` handle; None for a non-``local``
    scheme or an unparseable token. Deliberately host-blind — pure grammar,
    no liveness claim (the ``--stop``-style kill paths of same-host consumers
    parse with this; hostname scoping lives in ``resolve`` only)."""
    parsed = _parse_local(handle)
    return parsed[1] if parsed else None


def resolve(handle: str) -> bool | None:
    """Liveness of a handle token, actor-independently. True/False for a
    ``local://`` handle naming THIS host (via ``os.kill(pid, 0)``); None if
    the token isn't resolvable from here — a foreign scheme, or a
    ``local://`` handle for **another host** (probing the local pid table for
    a foreign pid would answer garbage — specs/lazy-launch.md; the caller
    falls back to heartbeat staleness).

    Best-effort: the bare-string probe is heuristic (PID reuse; ``?start=T``
    disambiguator deferred — see docs/backlog/conventions-hygiene.md F9).
    Provable liveness comes from a held OS handle (spawner, via
    ``LaunchHandle.is_alive()``) or heartbeat-staleness (observer tier 4)."""
    parsed = _parse_local(handle)
    if parsed is None:
        return None
    host, pid = parsed
    if host != socket.gethostname():
        return None   # not OUR pid table; never a false dead/alive
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, not ours
