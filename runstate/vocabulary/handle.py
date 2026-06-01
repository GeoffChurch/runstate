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
