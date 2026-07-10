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
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Protocol

from .channel import Channel, open_channel
from .vocabulary.payloads import Launched, Terminated, Topic
from .vocabulary.handle import local_handle
from .observables import live_demand, live_episode


class LaunchHandle(Protocol):
    """The observable surface of a launched worker, common to every launcher.

    ``run_id``/``channel`` locate the run's log; ``handle`` is the portable
    liveness token (§8). ``is_alive`` answers liveness, ``wait`` blocks until the
    worker finishes (and reaps it), ``terminate`` force-kills where the substrate
    allows (ThreadLauncher cannot, and raises). Launcher-specific extras live on
    the concrete handle (e.g. ThreadLauncher's ``.exception``).
    """

    run_id: str
    channel: Channel
    handle: str

    def is_alive(self) -> bool: ...
    def wait(self, timeout: float | None = None) -> Optional[int]: ...
    def terminate(self) -> None: ...


class Launcher(Protocol):
    """Spawn a worker into a run and bracket it with launcher.launched /
    launcher.terminated. ``open_channel`` is uniform; ``launch``'s *target* is
    launcher-specific by nature — an in-process callable for ThreadLauncher, a
    subprocess command for LocalLauncher — since how the worker receives its
    channel (passed directly vs re-derived via ``attach``) differs in kind.
    """

    def open_channel(self, run_id: str) -> Channel: ...
    def launch(self, run_id: str, target: object, **kwargs: object) -> LaunchHandle: ...


@dataclass
class _ThreadState:
    """Mutable holder for the exception the target raised (in-process debug aid)."""

    exc: BaseException | None = None


@dataclass
class _ThreadHandle:
    """The handle ThreadLauncher returns (concrete; the Launcher/LaunchHandle
    Protocols are extracted once LocalLauncher gives a second implementer)."""

    run_id: str
    channel: Channel
    handle: str
    _thread: threading.Thread
    _state: _ThreadState

    @property
    def exception(self) -> Optional[BaseException]:
        """The exception the target raised, if any (in-process debugging aid;
        the *fact* of an errored death is on the log as launcher.terminated)."""
        return self._state.exc

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> Optional[int]:
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
    def __init__(self, *, root: str | os.PathLike[str] | None = None, backend: str = "memory") -> None:
        self._root = root
        self._backend = backend

    def open_channel(self, run_id: str) -> Channel:
        return open_channel(run_id, root=self._root, backend=self._backend)

    def launch(self, run_id: str, target: Callable[..., object], *,
               args: tuple[Any, ...] = (), kwargs: dict[str, Any] | None = None) -> _ThreadHandle:
        """Run ``target(channel, *args, **kwargs)`` on a thread for ``run_id``.

        Brackets the work with launcher.launched / launcher.terminated. Returns
        immediately with a handle (the irreducible launch job, §8); reaping is
        the handle's ``join`` — the manner of death lands on the log either way.
        A raised exception maps to ``Terminated(reason="exited", exit_code=1)``:
        the 1 is a synthetic convention (a thread has no exit code), not a
        process exit status — a viewer should not read it as one.
        """
        kwargs = kwargs or {}
        channel = self.open_channel(run_id)
        handle = local_handle()
        channel.send(asdict(Launched(handle=handle)), topic=Launched.TOPIC)
        state = _ThreadState()

        def _run() -> None:
            try:
                target(channel, *args, **kwargs)
            except BaseException as exc:  # recorded on the log, not swallowed
                state.exc = exc
                channel.send(
                    asdict(Terminated(reason="exited", exit_code=1, signal=None)),
                    topic=Terminated.TOPIC,
                )
            else:
                channel.send(
                    asdict(Terminated(reason="exited", exit_code=0, signal=None)),
                    topic=Terminated.TOPIC,
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
    channel: Channel
    handle: str
    _proc: subprocess.Popen[bytes]
    _reaped: bool = field(default=False)
    # The seq of this child's launcher.launched record: the reap discipline's
    # claim check is scoped to starteds AFTER it, so an old episode's recycled
    # pid never reads as this child's claim (specs/lazy-launch.md).
    launched_seq: Optional[int] = None

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def wait(self, timeout: float | None = None) -> int:
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
        if rc == 0 and self._claimed_away():
            # The reap discipline (specs/lazy-launch.md): a clean exit from a
            # child that never claimed, while someone ELSE'S claim follows
            # our spawn, is a claim-race loser -- nobody. Writing terminated
            # would forge the run's terminal over the winner's live episode
            # (the launcher pairing is by latest record and `terminated`
            # carries no child identity). A null worker -- nobody claimed at
            # all -- keeps its record (terminated is its ONLY terminal), and
            # an unclean death keeps its record (startup-crash visibility).
            return
        if rc < 0:  # died from signal -rc
            body = asdict(Terminated(reason="killed", signal=-rc, exit_code=None))
        else:
            body = asdict(Terminated(reason="exited", exit_code=rc, signal=None))
        self.channel.send(body, topic=Terminated.TOPIC)

    def _claimed_away(self) -> bool:
        """True iff this child never claimed and a FOREIGN claim follows its
        own ``launched`` record — the silence is explained by someone else
        being the episode."""
        mine = foreign = False
        for e in self.channel.read(after=self.launched_seq or 0,
                                   topics=[Topic.LIFECYCLE_STARTED]):
            if e.body.get("handle") == self.handle:
                mine = True
            else:
                foreign = True
        return foreign and not mine


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

    def __init__(self, *, root: str | os.PathLike[str], backend: str = "sqlite") -> None:
        self._root = root
        self._backend = backend
        self._handles: list[_LocalHandle] = []

    def open_channel(self, run_id: str) -> Channel:
        return open_channel(run_id, root=self._root, backend=self._backend)

    def launch(self, run_id: str, cmd: list[str] | str, *,
               env: dict[str, str] | None = None) -> _LocalHandle:
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
        seq = channel.send(asdict(Launched(handle=handle)), topic=Launched.TOPIC)
        h = _LocalHandle(run_id=run_id, channel=channel, handle=handle, _proc=proc,
                         launched_seq=seq)
        self._handles.append(h)
        return h

    def reap(self) -> None:
        """Poll every outstanding handle, reaping finished children (each
        emits its ``launcher.terminated`` at most once, per the reap
        discipline). Load-bearing for a standing waker loop
        (specs/lazy-launch.md): an unreaped child is a POSIX zombie, and
        ``os.kill(pid, 0)`` *succeeds* on a zombie — a crashed service would
        read live to ``live_episode`` forever and never be re-woken."""
        for h in self._handles:
            h.poll()

    def __enter__(self) -> "LocalLauncher":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.reap()  # best-effort; don't block or kill stragglers


def relaunch_if_needed(launcher: Any, run_id: str, target: object,
                       **launch_kwargs: object) -> LaunchHandle | None:
    """Launch ``target`` into ``run_id`` only if no episode is currently live --
    a launcher-agnostic, best-effort single-spawn guard composed over a log read
    (``live_episode``) + ``launch``. Returns the new LaunchHandle, or None if a
    live episode already exists (no spawn). Correctness rests on the worker's
    self-claim (a check-to-spawn race just wastes a spawn that exits before
    acting); this only avoids that wasted spawn in the common already-live case.
    ``launch_kwargs`` is splatted into ``launch`` (launcher-specific, as sweep
    does): e.g. ``kwargs={...}`` for ThreadLauncher, ``env={...}`` for
    LocalLauncher."""
    channel = launcher.open_channel(run_id)
    if live_episode(channel) is not None:
        return None
    # `launcher` is intentionally `Any`: the two reference launchers have
    # genuinely different `launch` signatures (a callable `target` + args/kwargs
    # vs a `cmd` + env), so no single typed Protocol method admits both. We keep
    # the call dynamic here and re-assert the return type rather than force a
    # widening that would erase the launchers' own signatures.
    handle: LaunchHandle = launcher.launch(run_id, target, **launch_kwargs)
    return handle


def ensure_served(launcher: Any, run_id: str, target: object,
                  **launch_kwargs: object) -> LaunchHandle | None:
    """Wake a service iff there is live leased demand and no live episode —
    ``relaunch_if_needed``'s leased-demand sibling (two demand durabilities,
    two deciders — specs/lazy-launch.md). Returns the new LaunchHandle, or
    None (nothing needed: no demand, or already served; callers who must
    distinguish read the folds themselves).

    Caller-invoked: subscribe, then ``ensure_served`` — the demander's
    presence is already the keepalive (renewals), so its presence is also the
    waker. The standing-daemon form is this in a loop with a MANDATORY
    ``launcher.reap()`` per cycle. Conservative off-host: an episode whose
    handle this process cannot resolve reads live, and no wake happens.
    Correctness never depends on the pre-checks — the worker's birth-CAS
    arbitrates any double-spawn; the loser exits before acting, and the reap
    discipline keeps its corpse off the verdict plane. Never ``Watcher.add()``
    the returned handle (it may lose the claim race); ``observe()`` the run."""
    channel = launcher.open_channel(run_id)
    if not live_demand(channel):
        return None
    if live_episode(channel) is not None:
        return None
    # `launcher` is intentionally `Any`: the two reference launchers have
    # genuinely different `launch` signatures (a callable `target` + args/kwargs
    # vs a `cmd` + env), so no single typed Protocol method admits both. We keep
    # the call dynamic here and re-assert the return type rather than force a
    # widening that would erase the launchers' own signatures.
    handle: LaunchHandle = launcher.launch(run_id, target, **launch_kwargs)
    return handle
