"""Launch identity (docs/specs/launcher-record-identity.md).

A launcher mints ONE correlation id per launch and stamps it on the envelope's
``request_id`` for both of its records — ``launcher.launched`` (the spawn) and
``launcher.terminated`` (that spawn's death) — and the worker it spawns
re-emits the same id on its ``lifecycle.started``. So the claim that answers a
launch names the launch it answers, and a death names the launch that died.

That one id is what makes a third-party death record *attributable*. Without
it, ``terminated`` asserts the unknowable "the run is dead" (and a late reap
forges a live episode's verdict); with it, it asserts only "**my launch**
ended" — first-party to the launcher, about its own child — and a cold-log
reader can still say whose death it was, forever.

The id reaches the worker **ambiently**, the same way the run id does
(``current_channel``): ``RUNSTATE_LAUNCH_ID`` in the child's environment (the
cross-process, interop-relevant half — another language's launcher sets the
same variable), or a ContextVar bound around the target (the in-process half —
a thread launcher has no environment to inject into, and its threads share one
pid, so nothing *else* distinguishes its launches). The worker never
interprets the id; it only re-emits it.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

LAUNCH_ID_ENV = "RUNSTATE_LAUNCH_ID"

_current: ContextVar[str | None] = ContextVar("runstate_launch_id", default=None)


def new_launch_id() -> str:
    """Mint an id for one launch. Opaque and unique per launch — deliberately
    NOT derived from the pid (a thread launcher's spawns share one) nor from
    log position (a slow claim can land after a later launch, so position does
    not attribute)."""
    return uuid.uuid4().hex


@contextmanager
def launch_scope(launch_id: str) -> Iterator[None]:
    """Bind ``launch_id`` for whatever this thread runs inside the block — the
    in-process transport (``ThreadLauncher`` wraps its target in it, so a
    ``Worker`` the target constructs picks the id up)."""
    token = _current.set(launch_id)
    try:
        yield
    finally:
        _current.reset(token)


def current_launch_id() -> str | None:
    """The id of the launch that spawned this worker, or None if no launcher did
    (a hand-run worker — honest information, not a degenerate case: its episode
    simply has no launcher records to pair with).

    An in-process binding beats the inherited environment: a ``ThreadLauncher``
    running *inside* a launched process must stamp its own launch's id, not the
    parent process's."""
    bound = _current.get()
    if bound is not None:
        return bound
    return os.environ.get(LAUNCH_ID_ENV) or None
