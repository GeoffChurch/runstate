"""The reference launchers and the death reports of log format 0.4.0, staged
(docs/specs/commits-and-lineage.md §3, "How a claim ended: two viewpoints",
supervisor-v0.5, "Who writes a death report", "The helpers", "The writers"; §5, the
deciders).

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module over
``runstate/launcher.py``.

TWO VIEWPOINTS. A claim's end is told from two sides. The worker's exit
(``lifecycle.stopped``) is its own word. A death report (``supervisor.died``) is a
third party's, naming the claim and carrying its evidence, on the external plane
beside ``supervisor.terminated``, which names a launch. Either ends the claim, and
no fold matches a ``terminated`` to a claim: whether a launch's process is the
claimant is a fact only its launcher can check, so the launcher writes the death
report where it can (§2).

EVIDENCE, NEVER INFERENCE (§3). A reap's exit status, a same-host probe that finds
the claimant's process gone, an accounting record, and a backend's session probe
that finds the claim's writing session ended are evidence. Staleness, silence and a
cross-host absence are not: a slow worker is alive, and a report would fence it. A
``lost`` report says only that the claim ended, so it lands only while the claim is
held; a report with a cause (a signal, a nonzero exit, a raise, accounting) lands
whether or not it has ended, and the verdict keeps every report (§5).

A READ NEVER WRITES. No fold, ``peek_terminal`` or ``is_alive()`` reports. A party
that wants its probe recorded calls ``report_if_dead``; the deciders here do,
before they launch. The one exception is a reap: ``waitpid`` yields the exit status
once, so whoever reaps records it there.

``report_death``'s CAS at its W is what makes the held check sound: the rule is
checked on exactly the records the append follows (§2, "a record its writer
CAS-appends at W follows every record at or below W").
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Protocol

from ..channel import Channel, Envelope, RunNotFound, attach_channel, create_channel
from ..channel.base import SessionProbe
from ..vocabulary.handle import local_handle, resolve
from ..vocabulary.launch import LAUNCH_ID_ENV, launch_scope, new_launch_id
from .observables import (
    LiveDemand,
    Tier,
    claim_verdict,
    latest_episode,
    live_episode,
)
from .vocabulary.payloads import (
    Died,
    Launched,
    Outcome,
    Reap,
    Started,
    Terminated,
    Topic,
)
from .walk import verdict_parse

# ----- the helpers (§3, "The helpers") ---------------------------------------------


class ClaimAliveError(Exception):
    """``report_death`` refused: the claim's handle probes alive, and the caller did
    not pass ``force=True``, its statement that its evidence outranks the probe.
    A report would fence a live worker (§4), turning a slow run into a dead one."""

    def __init__(self, claim_seq: int, handle: str) -> None:
        super().__init__(
            f"claim {claim_seq}'s handle {handle} probes alive: no death report "
            "(pass force=True when the evidence outranks the probe)"
        )
        self.claim_seq = claim_seq
        self.handle = handle


def report_death(
    ch: Channel, claim_seq: int, *, outcome: Outcome, evidence: str, force: bool
) -> int | None:
    """Report the death of the claim ``claim_seq`` names, from ``evidence`` (§3):
    the seq of the ``supervisor.died`` it appends, or ``None`` when there is nothing
    to report.

    - ``ValueError`` when the record at ``claim_seq``, at the W it reads, is not a
      ``lifecycle.started``, when ``outcome`` is not a death's
      (``Outcome.deaths()``: never a completion, never a presumption), or when
      ``evidence`` is empty. ``evidence`` says who reports and from what, prefixed
      by its kind (``slurm:``, ``reap:`` ...), since ``died`` has no author field;
    - ``None`` when the claim has a readable exit: its self-report outranks every
      report (§5). An unreadable exit is no self-report, so a report still lands;
    - ``None`` for a ``lost`` report on a claim that has ended: evidence without a
      cause reports only while the claim is held;
    - ``ClaimAliveError`` when the claim's handle probes alive, unless ``force``.
      A dead probe, or one that cannot tell (another host), does not refuse.

    A claim a death report already ended is reported again, so each holder of
    evidence records it once (review §12, item 14). ``t`` is this call's clock, when
    its caller saw the death. The append is a CAS at W, and a lost CAS reads again,
    so each rule is checked at the W of the append."""
    return _report(
        ch,
        Died(claim_seq=claim_seq, outcome=outcome, evidence=evidence, t=time.time()),
        force=force,
    )


def _report(ch: Channel, died: Died, *, force: bool) -> int | None:
    """``report_death`` of a body already built (and so already checked): a reap
    dates its report by the death it reaped, the ``t`` of its ``terminated``."""
    while True:
        w = ch.last_seq()
        claim = _claim_at(ch, died.claim_seq, w)
        verdict = claim_verdict(ch, claim, w=w)
        if verdict is not None and (
            verdict.tier == Tier.EXIT or died.outcome == Outcome.LOST
        ):
            return None
        if not force:
            handle = verdict_parse(Started, claim).handle
            if resolve(handle) is True:
                raise ClaimAliveError(died.claim_seq, handle)
        seq = ch.send(asdict(died), topic=Died.TOPIC, expected_seq=w)
        if seq is not None:
            return seq


def _claim_at(ch: Channel, claim_seq: int, w: int) -> Envelope:
    """The claim at ``claim_seq``, at or below ``w``; ``ValueError`` if there is
    none: a report naming no claim would end nothing any reader can name."""
    got = ch.read(after=claim_seq - 1, limit=1) if claim_seq <= w else []
    if not got or got[0].seq != claim_seq or got[0].topic != Topic.LIFECYCLE_STARTED:
        raise ValueError(f"no lifecycle.started at seq {claim_seq} of {w}")
    return got[0]


def report_if_dead(ch: Channel) -> int | None:
    """The probe's report (§3): when the newest claim is held, and either the
    channel's session probe finds ended the session that wrote it, or its handle is
    on this host and probes dead, report it ``lost`` and return the report's seq;
    else ``None``.

    - **The session** (``SessionProbe``, Postgres): evidence that the claim can
      write nothing more, since a channel never reconnects and every record naming
      a claim travels on the claim's session. It is not about the process, which
      may outlive its session, so it outranks a live pid and passes ``force=True``.
      Evidence ``session: <server>/<key>``. A probe that cannot tell (another
      server, a standby, a migrated row) is no evidence;
    - **the handle**: a same-host probe that finds the process gone. Evidence
      ``probe: <handle>``. The report probes again before its CAS, and when the pid
      has been reused in between, which the report's ``ClaimAliveError`` says, it
      reports nothing: the second probe is the later evidence.

    Staleness and a cross-host handle are never evidence (§3)."""
    claim = latest_episode(ch)
    if claim is None or claim_verdict(ch, claim, w=ch.last_seq()) is not None:
        return None
    if isinstance(ch, SessionProbe):
        session = ch.writer_session(claim.seq)
        if session is not None and ch.session_ended(session) is True:
            return report_death(
                ch,
                claim.seq,
                outcome=Outcome.LOST,
                evidence=f"session: {session.server}/{session.key}",
                force=True,
            )
    handle = verdict_parse(Started, claim).handle
    if resolve(handle) is not False:
        return None
    try:
        return report_death(
            ch,
            claim.seq,
            outcome=Outcome.LOST,
            evidence=f"probe: {handle}",
            force=False,
        )
    except ClaimAliveError:
        return None


def _launch_claim(ch: Channel, launch_id: str, *, after: int) -> Envelope | None:
    """The newest claim naming the launch ``launch_id``, above ``after``: the log's
    last seq before the launch began, since its claimant cannot claim before it is
    spawned. The ``request_ids`` filter also admits records naming no request, so
    the envelope's id is compared here (specs/launcher-record-identity.md)."""
    named = [
        e
        for e in ch.read(
            after=after, topics=[Topic.LIFECYCLE_STARTED], request_ids=[launch_id]
        )
        if e.request_id == launch_id
    ]
    return named[-1] if named else None


# ----- the launchers (§3, "The writers") -------------------------------------------


class LaunchHandle(Protocol):
    """The observable surface of a launched worker, common to every launcher.

    ``run_id``/``channel`` locate the run's log; ``handle`` is the portable
    liveness token; ``launch_id`` the launch's correlation id, which its
    ``supervisor.*`` records carry and its claimant's ``lifecycle.started``
    re-emits (specs/launcher-record-identity.md). ``is_alive`` answers liveness
    and writes nothing (§3, "A read never writes"); ``wait`` blocks until the
    worker finishes, and reaps it; ``terminate`` force-kills where the substrate
    allows (``ThreadLauncher`` cannot, and raises)."""

    run_id: str
    channel: Channel
    handle: str
    launch_id: str

    def is_alive(self) -> bool: ...
    def wait(self, timeout: float | None = None) -> Optional[int]: ...
    def terminate(self) -> None: ...


class Launcher(Protocol):
    """Spawn a worker into a run and bracket it with ``supervisor.launched`` /
    ``supervisor.terminated``, and a ``supervisor.died`` where the launch's end is
    evidence of its claimant's. ``create_channel`` births the run (the launch);
    ``attach_channel`` reads an existing one (the deciders, which treat
    ``RunNotFound`` as "not launched yet"). ``launch``'s *target* is
    launcher-specific by nature: a callable for ``ThreadLauncher``, a command for
    ``LocalLauncher``."""

    def create_channel(self, run_id: str) -> Channel: ...
    def attach_channel(self, run_id: str) -> Channel: ...
    def launch(self, run_id: str, target: object, **kwargs: object) -> LaunchHandle: ...


def _launched(ch: Channel, handle: str, launch_id: str) -> None:
    body = Launched(handle=handle, status="running", t=time.time())
    ch.send(asdict(body), topic=Launched.TOPIC, request_id=launch_id)


@dataclass
class _ThreadState:
    """Mutable holder for the exception the target raised (in-process debug aid)."""

    exc: BaseException | None = None


@dataclass
class _ThreadHandle:
    """The handle ``ThreadLauncher`` returns. Its ``launch_id`` is the only thing
    that tells two thread launches apart: they share the launcher's pid, and so
    one handle."""

    run_id: str
    channel: Channel
    handle: str
    _thread: threading.Thread
    _state: _ThreadState
    launch_id: str

    @property
    def exception(self) -> Optional[BaseException]:
        """The exception the target raised, if any (an in-process debugging aid;
        the fact is on the log, as ``terminated`` and the ``died`` it implies)."""
        return self._state.exc

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> Optional[int]:
        """Block until the worker thread finishes. ``None``: a thread has no exit
        code; the manner of its end is on the log."""
        self._thread.join(timeout)
        return None

    def terminate(self) -> None:
        raise NotImplementedError(
            "ThreadLauncher cannot force-terminate a thread; send a cooperative "
            "control.stop instead (or use LocalLauncher for a killable subprocess)"
        )


class ThreadLauncher:
    """Run a worker target on a thread of this process (in-process reference).

    ``backend`` defaults to the memory backend: it says where a log lives, not what
    it holds, so its default changes no result (plan, "Exempt defaults")."""

    def __init__(
        self, *, root: str | os.PathLike[str] | None = None, backend: str = "memory"
    ) -> None:
        self._root = root
        self._backend = backend

    def create_channel(self, run_id: str) -> Channel:
        return create_channel(run_id, root=self._root, backend=self._backend)

    def attach_channel(self, run_id: str) -> Channel:
        return attach_channel(run_id, root=self._root, backend=self._backend)

    def launch(
        self,
        run_id: str,
        target: Callable[..., object],
        *,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> _ThreadHandle:
        """Run ``target(channel, *args, **kwargs)`` on a thread for ``run_id``,
        bracketed by ``supervisor.launched`` and ``supervisor.terminated``, both
        naming the launch. ``launch_scope`` binds the launch id for the target, so
        the claim its Worker writes names the launch.

        When the thread finishes, the newest claim naming the launch, attributed by
        the id alone since a thread cannot be told apart by pid, gets a death
        report unless it has a readable exit (§3, "The writers"): a raise is
        ``errored`` (``reap: <ExceptionType>: <message>``), a return ``lost``
        (``reap: returned``), which lands only while the claim is held. It passes
        ``force=True``: the claim's handle names this live process, and a
        finished thread is definitive. A target is run synchronously on the
        thread, so its claim, if it made one, has landed by then; a launch nothing
        claimed writes ``terminated`` only, which is its run's verdict (§5).

        A raise's ``terminated`` carries exit code 1, a synthetic convention: a
        thread has no exit code."""
        call_kwargs = kwargs or {}
        channel = self.create_channel(run_id)
        handle = local_handle()
        launch_id = new_launch_id()
        floor = channel.last_seq()
        _launched(channel, handle, launch_id)
        state = _ThreadState()

        def _ended(exit_code: int, outcome: Outcome, evidence: str) -> None:
            t = time.time()
            end = Terminated(reason=Reap.EXITED, exit_code=exit_code, signal=None, t=t)
            channel.send(asdict(end), topic=Terminated.TOPIC, request_id=launch_id)
            claim = _launch_claim(channel, launch_id, after=floor)
            if claim is not None:
                died = Died(
                    claim_seq=claim.seq, outcome=outcome, evidence=evidence, t=t
                )
                _report(channel, died, force=True)

        def _run() -> None:
            try:
                with launch_scope(launch_id):
                    target(channel, *args, **call_kwargs)
            except BaseException as exc:  # recorded on the log, not swallowed
                state.exc = exc
                _ended(1, Outcome.ERRORED, f"reap: {type(exc).__name__}: {exc}")
            else:
                _ended(0, Outcome.LOST, "reap: returned")

        thread = threading.Thread(target=_run, daemon=True)
        h = _ThreadHandle(
            run_id=run_id,
            channel=channel,
            handle=handle,
            _thread=thread,
            _state=state,
            launch_id=launch_id,
        )
        thread.start()
        return h


@dataclass
class _LocalHandle:
    """The handle ``LocalLauncher`` returns. It holds the child's ``Popen``, so its
    liveness, its reap and a kill go through it, with no pid-reuse window; the
    portable ``handle`` string is for other observers, which hold no ``Popen``."""

    run_id: str
    channel: Channel
    handle: str
    _proc: subprocess.Popen[bytes]
    launch_id: str
    _floor: int  # the log's last seq before the spawn: the claim lands above it
    _reaped: bool = field(default=False)

    def is_alive(self) -> bool:
        """Pure: it reads the ``Popen``, and writes nothing, even when the child
        has exited; the reap of ``wait`` or ``poll`` writes."""
        return self._proc.poll() is None

    def wait(self, timeout: float | None = None) -> int:
        """Block until the child exits, reap it, and return its exit code."""
        rc = self._proc.wait(timeout)
        self._reap()
        return rc

    def poll(self) -> Optional[int]:
        """Reap if the child has exited; non-blocking. The exit code, or ``None``
        while it runs."""
        rc = self._proc.poll()
        if rc is not None:
            self._reap()
        return rc

    def terminate(self) -> None:
        """Send SIGTERM. The reap, and its records, follow on ``wait`` or ``poll``."""
        self._proc.terminate()

    def _own_claim(self) -> Envelope | None:
        """The newest claim naming this launch, if its claimant is this child: its
        handle is the launch's whole ``local://host/pid``, compared as the
        migration compares it (§7). A wrapper's grandchild that claims, as under
        ``sbatch`` or ``sh -c``, is not: the child's end says nothing of it."""
        claim = _launch_claim(self.channel, self.launch_id, after=self._floor)
        if claim is None or verdict_parse(Started, claim).handle != self.handle:
            return None
        return claim

    def _orphaned(self) -> bool:
        """Is this child the claimant of a claim a death report ended (§3)? Its
        session may be gone while it lives, so it can write nothing more, and a
        successor would run beside it. Its own exit ending the claim is no orphan:
        it is finishing."""
        claim = self._own_claim()
        if claim is None:
            return False
        verdict = claim_verdict(self.channel, claim, w=self.channel.last_seq())
        return verdict is not None and verdict.tier == Tier.DIED

    def _reap(self) -> None:
        """Record this child's end, exactly once: its ``terminated``,
        unconditionally, since a launcher reports what its own child did and the
        launch id says whose end it is; then, when the child is the claimant
        (``_own_claim``) and its claim has no readable exit, a death report, the
        only place this launcher writes one (§3, "The writers"):

        - a signal is ``killed`` (``reap: signal <s>``) and a nonzero exit
          ``errored`` (``reap: exit <n>``), whether or not the claim has ended;
        - an exit 0 is ``lost`` (``reap: exit 0``), only while the claim is held: a
          worker a mistaken report released exits 0, and adds nothing to it.

        It passes ``force=True``: ``waitpid`` is definitive, and the freed pid may
        already be reused. One reap, one ``t``, bound when the death is observed
        (observer-clock §3), on both records."""
        rc = self._proc.returncode
        if self._reaped or rc is None:
            return
        self._reaped = True
        t = time.time()
        if rc < 0:  # died from signal -rc
            end = Terminated(reason=Reap.KILLED, exit_code=None, signal=-rc, t=t)
            outcome, evidence = Outcome.KILLED, f"reap: signal {-rc}"
        else:
            end = Terminated(reason=Reap.EXITED, exit_code=rc, signal=None, t=t)
            outcome = Outcome.ERRORED if rc else Outcome.LOST
            evidence = f"reap: exit {rc}"
        self.channel.send(
            asdict(end), topic=Terminated.TOPIC, request_id=self.launch_id
        )
        claim = self._own_claim()
        if claim is not None:
            died = Died(claim_seq=claim.seq, outcome=outcome, evidence=evidence, t=t)
            _report(self.channel, died, force=True)


class LocalLauncher:
    """Spawn workers as local subprocesses.

    ``launch`` runs a command with ``RUNSTATE_*`` in its environment; the child
    calls ``runstate.current_channel()`` to open the same run's channel, so the
    backend must be shared across processes (sqlite by default: ``backend`` says
    where a log lives, not what it holds, so its default changes no result). As a
    context manager it reaps on exit (``reap``), and does not block on stragglers.
    """

    def __init__(
        self, *, root: str | os.PathLike[str], backend: str = "sqlite"
    ) -> None:
        self._root = root
        self._backend = backend
        self._handles: list[_LocalHandle] = []

    def create_channel(self, run_id: str) -> Channel:
        return create_channel(run_id, root=self._root, backend=self._backend)

    def attach_channel(self, run_id: str) -> Channel:
        return attach_channel(run_id, root=self._root, backend=self._backend)

    def launch(
        self, run_id: str, cmd: list[str] | str, *, env: dict[str, str] | None = None
    ) -> _LocalHandle:
        channel = self.create_channel(run_id)
        launch_id = new_launch_id()
        floor = channel.last_seq()
        child_env = {
            **os.environ,
            **(env or {}),
            "RUNSTATE_RUN_ID": run_id,
            "RUNSTATE_CHANNEL_ROOT": str(self._root),
            "RUNSTATE_CHANNEL_BACKEND": self._backend,
            # The child's claim re-emits it, so it names the launch that spawned it.
            LAUNCH_ID_ENV: launch_id,
        }
        proc = subprocess.Popen(cmd, env=child_env)
        handle = f"local://{socket.gethostname()}/{proc.pid}"
        _launched(channel, handle, launch_id)
        h = _LocalHandle(
            run_id=run_id,
            channel=channel,
            handle=handle,
            _proc=proc,
            launch_id=launch_id,
            _floor=floor,
        )
        self._handles.append(h)
        return h

    def reap(self) -> None:
        """Reap every finished child, each writing its records at most once, and
        terminate, through its ``Popen``, a live child of its own whose claim a
        death report ended: an orphan whose session is gone, which a successor
        would otherwise run beside (§3, "A session probe"). Only this kills, and
        never by pid, which pid reuse makes unsafe (§12; plan, open question 6);
        the deciders never kill. The child's own reap, on a later call, records
        the signal.

        Load-bearing for a standing waker loop (specs/lazy-launch.md): an unreaped
        child is a zombie, its pid probes alive, and its death goes unreported."""
        for h in self._handles:
            if h.poll() is None and h._orphaned():
                h.terminate()

    def __enter__(self) -> LocalLauncher:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.reap()  # best-effort; don't block on stragglers


# ----- the deciders (§3, §5) -------------------------------------------------------


def relaunch_if_needed(
    launcher: Any, run_id: str, target: object, **launch_kwargs: object
) -> LaunchHandle | None:
    """Launch ``target`` into ``run_id`` unless its newest claim is held (§5): the
    durable-demand decider. A held claim is first given to ``report_if_dead``
    (§3, "The writers"): when its report lands, the claim has ended and it
    launches; when nothing reports, it returns ``None``. It never kills.

    A best-effort single-spawn guard over a log read: correctness rests on the
    worker's claim CAS, and a check-to-spawn race wastes a spawn. ``launcher`` is
    ``Any`` because the reference launchers' ``launch`` signatures differ in kind
    (a callable with ``args``/``kwargs``, a command with ``env``), and
    ``launch_kwargs`` is splatted into it."""
    try:
        with launcher.attach_channel(run_id) as channel:  # a probe handle: close it
            if live_episode(channel) is not None and report_if_dead(channel) is None:
                return None
    except RunNotFound:
        pass  # no records: nothing claimed, so nothing is held
    handle: LaunchHandle = launcher.launch(run_id, target, **launch_kwargs)
    return handle


def ensure_served(
    launcher: Any,
    run_id: str,
    target: object,
    *,
    demand: LiveDemand,
    **launch_kwargs: object,
) -> LaunchHandle | None:
    """Wake a service when there is live demand and no held claim (§5;
    specs/lazy-launch.md): ``relaunch_if_needed``'s leased-demand sibling. Returns
    the new handle, or ``None``: no demand, or a claim held that nothing reported.

    ``demand`` is the caller's ``LiveDemand`` fold for this run, held across calls
    so each poll reads only new records (plan, open question 3); a one-shot caller
    passes a fresh one. A held claim is first given to ``report_if_dead``, and when
    its report lands the demand is read again, since the report may have voided
    the only lease: a lease is per claim, and void once its claim has ended (§2).
    It never kills.

    The standing form is this in a loop with a mandatory ``launcher.reap()`` per
    cycle. A claim whose handle is on another host and whose session nothing
    probes stays held: no wake. Never ``Watcher.add()`` the returned handle (it may
    lose the claim race); observe the run."""
    try:
        with launcher.attach_channel(run_id) as channel:  # a probe handle: close it
            if not demand.poll(channel):
                return None
            if live_episode(channel) is not None:
                if report_if_dead(channel) is None or not demand.poll(channel):
                    return None
    except RunNotFound:
        return None  # no records: no demand
    handle: LaunchHandle = launcher.launch(run_id, target, **launch_kwargs)
    return handle
