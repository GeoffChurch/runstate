"""Reference launchers (docs/design-v0.2.md §8-9).

A launcher spawns a worker *into a run* and records the **process-level**
lifecycle on the log: ``launcher.launched`` (spawn-intent + the liveness handle)
when it starts, and ``launcher.terminated`` (the *manner* of death) when the
worker process/thread ends. This is distinct from the worker's own
``lifecycle.*`` (its cooperative, semantic view): a crash that prevents a clean
``lifecycle.stopped`` is still caught by ``launcher.terminated`` (§8).

``ThreadLauncher`` is the in-process reference — it runs the target on a thread
in the *same* process and hands it the run's channel directly (memory backend by
default; no env round-trip). Two degeneracies are intrinsic to threads, and are
what ``LocalLauncher`` (subprocess) exists to resolve: a thread cannot be
force-terminated, and its liveness cannot be resolved from the portable handle
(the handle's pid is the launcher's own process). So here ``is_alive`` consults
the thread object and ``terminate`` is unavailable.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from .channel import open_channel
from .handle import local_handle


@dataclass
class _ThreadHandle:
    """The handle ThreadLauncher returns (concrete; the Launcher/LaunchHandle
    Protocols are extracted once LocalLauncher gives a second implementer)."""

    run_id: str
    channel: object
    handle: str
    _thread: threading.Thread
    _state: dict

    @property
    def exception(self) -> Optional[BaseException]:
        """The exception the target raised, if any (in-process debugging aid;
        the *fact* of an errored death is on the log as launcher.terminated)."""
        return self._state.get("exc")

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout=None) -> None:
        self._thread.join(timeout)

    def terminate(self) -> None:
        raise NotImplementedError(
            "ThreadLauncher cannot force-terminate a thread; send a cooperative "
            "control.stop instead (or use LocalLauncher for a killable subprocess)"
        )


class ThreadLauncher:
    def __init__(self, *, root=None, backend: str = "memory"):
        self._root = root
        self._backend = backend

    def open_channel(self, run_id):
        return open_channel(run_id, root=self._root, backend=self._backend)

    def launch(self, run_id, target, *, args=(), kwargs=None) -> _ThreadHandle:
        """Run ``target(channel, *args, **kwargs)`` on a thread for ``run_id``.

        Brackets the work with launcher.launched / launcher.terminated. Returns
        immediately with a handle (the irreducible launch job, §8); reaping is
        the handle's ``join`` — the manner of death lands on the log either way.
        """
        kwargs = kwargs or {}
        channel = self.open_channel(run_id)
        handle = local_handle()
        channel.send({"handle": handle, "status": "running"}, topic="launcher.launched")
        state: dict = {"exc": None}

        def _run():
            try:
                target(channel, *args, **kwargs)
            except BaseException as exc:  # recorded on the log, not swallowed
                state["exc"] = exc
                channel.send(
                    {"exit_code": 1, "reason": "exited"}, topic="launcher.terminated"
                )
            else:
                channel.send(
                    {"exit_code": 0, "reason": "exited"}, topic="launcher.terminated"
                )

        thread = threading.Thread(target=_run, daemon=True)
        h = _ThreadHandle(
            run_id=run_id, channel=channel, handle=handle, _thread=thread, _state=state
        )
        thread.start()
        return h
