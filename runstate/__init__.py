"""runstate: cooperative bidirectional control protocol for long-running workers.

The library has three public surfaces:

1. The Channel substrate (durable per-run IPC):
       from runstate import Channel, open_channel, attach

2. The opt-in typed control vocabulary (orchestrator → worker):
       from runstate import control
       control.send_stop(ch); control.send_stop_at_step(ch, 200)
       cmd = control.check(ch, current_step=step)

3. The opt-in typed event vocabulary (worker → orchestrator):
       from runstate import events
       events.progress(ch, step=step, metrics={"loss": loss})
       events.stopped(ch, reason="natural")

The protocol's wire format is defined in protocol/messages-v0.1.schema.json;
the prose semantics live in protocol/spec.md.
"""

from __future__ import annotations

import os
from typing import Literal, Optional

from .channel import Channel, open_channel
from . import control, events

__version__ = "0.1.0"

__all__ = [
    "Channel",
    "open_channel",
    "attach",
    "control",
    "events",
    "__version__",
]


def attach(
    run_id: Optional[str] = None,
    *,
    root: Optional[str] = None,
    backend: Optional[Literal["file", "sqlite"]] = None,
) -> Channel:
    """Worker-side: open the worker-role Channel for this run.

    Reads RUNSTATE_RUN_ID, RUNSTATE_CHANNEL_ROOT, RUNSTATE_CHANNEL_BACKEND
    from the environment if any argument is None. Raises RuntimeError if
    the env vars are missing and no explicit args provided.

    Pass explicit arguments to attach standalone (e.g., for debugging
    outside an orchestrator).
    """
    if run_id is None:
        run_id = os.environ.get("RUNSTATE_RUN_ID")
        if run_id is None:
            raise RuntimeError(
                "attach() called without run_id and RUNSTATE_RUN_ID is not set"
            )
    if root is None:
        root = os.environ.get("RUNSTATE_CHANNEL_ROOT")
        if root is None:
            raise RuntimeError(
                "attach() called without root and RUNSTATE_CHANNEL_ROOT is not set"
            )
    if backend is None:
        env_backend = os.environ.get("RUNSTATE_CHANNEL_BACKEND", "file")
        if env_backend not in ("file", "sqlite"):
            raise RuntimeError(
                f"RUNSTATE_CHANNEL_BACKEND must be 'file' or 'sqlite', got {env_backend!r}"
            )
        backend = env_backend  # type: ignore[assignment]
    return open_channel(run_id, role="worker", root=root, backend=backend)
