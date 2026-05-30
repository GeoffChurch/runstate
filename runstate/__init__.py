"""runstate — cooperative control protocol for long-running scientific workers.

v0.2 (in progress): a per-run topic-log **substrate** with opt-in **conventions**
(cooperative-control, subscription, lifecycle, launcher). See docs/design-v0.2.md.
"""

import os

from .channel import Envelope, open_channel

__version__ = "0.2.0.dev0"


def attach(run_id=None, *, root=None, backend=None):
    """Worker-side: open the channel for the run this process was launched into.

    A Launcher sets ``RUNSTATE_RUN_ID`` / ``RUNSTATE_CHANNEL_ROOT`` /
    ``RUNSTATE_CHANNEL_BACKEND`` in the worker's environment; ``attach()`` reads
    them. Explicit arguments override the environment. Mirrors how the
    orchestrator named the run, so both ends meet on the same log.
    """
    if run_id is None:
        run_id = os.environ["RUNSTATE_RUN_ID"]
    if root is None:
        root = os.environ.get("RUNSTATE_CHANNEL_ROOT")
    if backend is None:
        backend = os.environ.get("RUNSTATE_CHANNEL_BACKEND", "sqlite")
    return open_channel(run_id, root=root, backend=backend)


__all__ = ["__version__", "attach", "open_channel", "Envelope"]
