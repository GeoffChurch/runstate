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

import os
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .channel import open_channel
from .handle import local_handle


class LaunchHandle(Protocol):
    """The observable surface of a launched worker, common to every launcher.

    ``run_id``/``channel`` locate the run's log; ``handle`` is the portable
    liveness token (§8). ``is_alive`` answers liveness, ``wait`` blocks until the
    worker finishes (and reaps it), ``terminate`` force-kills where the substrate
    allows (ThreadLauncher cannot, and raises). Launcher-specific extras live on
    the concrete handle (e.g. ThreadLauncher's ``.exception``).
    """

    run_id: str
    channel: object
    handle: str

    def is_alive(self) -> bool: ...
    def wait(self, timeout=None) -> Optional[int]: ...
    def terminate(self) -> None: ...


class Launcher(Protocol):
    """Spawn a worker into a run and bracket it with launcher.launched /
    launcher.terminated. ``open_channel`` is uniform; ``launch``'s *target* is
    launcher-specific by nature — an in-process callable for ThreadLauncher, a
    subprocess command for LocalLauncher — since how the worker receives its
    channel (passed directly vs re-derived via ``attach``) differs in kind.
    """

    def open_channel(self, run_id) -> object: ...
    def launch(self, run_id, target, **kwargs) -> LaunchHandle: ...


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

    def wait(self, timeout=None) -> Optional[int]:
        """Block until the worker thread finishes. Returns None — a thread has no
        exit code; the manner of death is on the log (launcher.terminated) and
        the raised exception, if any, is on ``.exception``."""
        self._thread.join(timeout)
        return None

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


@dataclass
class _LocalHandle:
    """The handle LocalLauncher returns. Holds the child's Popen, so liveness is
    read from it directly (reliable, no PID-reuse window); the portable handle
    string is for *other* observers that resolve it without the Popen (§8)."""

    run_id: str
    channel: object
    handle: str
    _proc: subprocess.Popen
    _reaped: bool = field(default=False)

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def wait(self, timeout=None) -> int:
        """Block until the child exits, reap it (emit launcher.terminated once),
        and return its exit code."""
        rc = self._proc.wait(timeout)
        self._reap()
        return rc

    def poll(self) -> Optional[int]:
        """Reap if the child has exited; non-blocking. Returns the exit code or
        None if still running."""
        rc = self._proc.poll()
        if rc is not None:
            self._reap()
        return rc

    def terminate(self) -> None:
        """Send SIGTERM. Reaping (and launcher.terminated) follows on wait/poll."""
        self._proc.terminate()

    def _reap(self) -> None:
        rc = self._proc.returncode
        if self._reaped or rc is None:
            return
        self._reaped = True
        if rc < 0:  # died from signal -rc
            body = {"signal": -rc, "reason": "killed"}
        else:
            body = {"exit_code": rc, "reason": "exited"}
        self.channel.send(body, topic="launcher.terminated")


class LocalLauncher:
    """Spawn workers as local subprocesses (the full handle story, §8).

    ``launch`` runs a command with ``RUNSTATE_*`` injected; the child calls
    ``runstate.attach()`` to re-derive the same run's channel — so the backend
    must be cross-process durable (sqlite, the default; memory would not be
    shared across processes). As a context manager, best-effort reaps any
    finished children on exit (it does not block on or kill stragglers — that
    stays the caller's choice via ``wait`` / ``terminate``, honoring §8's
    fire-and-forget split).
    """

    def __init__(self, *, root, backend: str = "sqlite"):
        self._root = root
        self._backend = backend
        self._handles: list[_LocalHandle] = []

    def open_channel(self, run_id):
        return open_channel(run_id, root=self._root, backend=self._backend)

    def launch(self, run_id, cmd, *, env=None) -> _LocalHandle:
        channel = self.open_channel(run_id)
        child_env = {
            **os.environ,
            **(env or {}),
            "RUNSTATE_RUN_ID": run_id,
            "RUNSTATE_CHANNEL_ROOT": str(self._root),
            "RUNSTATE_CHANNEL_BACKEND": self._backend,
        }
        proc = subprocess.Popen(cmd, env=child_env)
        handle = f"local://{socket.gethostname()}/{proc.pid}"
        channel.send({"handle": handle, "status": "running"}, topic="launcher.launched")
        h = _LocalHandle(run_id=run_id, channel=channel, handle=handle, _proc=proc)
        self._handles.append(h)
        return h

    def __enter__(self) -> "LocalLauncher":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        for h in self._handles:
            h.poll()  # reap whatever has finished; don't block or kill stragglers
        return False
