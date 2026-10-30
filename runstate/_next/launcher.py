"""The reference launchers and the death reports of log format 0.4.0, staged
(docs/specs/commits-and-lineage.md §3, "How a claim ended: two viewpoints",
supervisor-v0.5, "Who writes a death report", "The helpers", "The writers"; §5, the
deciders).

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module over
``runstate/launcher.py``.

A LAUNCHER IS TYPED BY ITS TARGET (plan, open question 18). ``Launcher[T]`` is a
Strategy resolved where the caller reads its configuration: ``ThreadLauncher``
launches a ``ThreadTarget`` and ``LocalLauncher`` a ``Command``, each a frozen
record of everything that changes the worker's result. The deciders, and the
producer and the sweep built on them, take a ``Launcher[T]`` and its ``T``, so a
target the launcher does not run is a type error. A target a drive can aim is
``Drivable``.

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

import functools
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import KW_ONLY, asdict, dataclass, field, replace
from enum import Enum
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Optional, Protocol, Self, TypeVar

from ..channel import Channel, Envelope, RunNotFound, attach_channel, create_channel
from ..channel.base import SessionProbe
from ..vocabulary.handle import local_handle, local_handle_of, resolve
from ..vocabulary.launch import LAUNCH_ID_ENV, launch_scope, new_launch_id
from .claims import endings, has_exit
from .observables import LiveDemand, latest_episode, live_episode
from .vocabulary.coordinate import Target
from .vocabulary.payloads import (
    Died,
    Launched,
    Outcome,
    Reap,
    Started,
    Terminated,
    Topic,
)
from .walk import readable

# ----- the helpers (§3, "The helpers") ---------------------------------------------


# The attempts ``_report`` makes before it gives up. Its CAS loses only to a record
# that landed inside one round trip of its loop (``last_seq``, the claim's point read,
# its window's read, perhaps a probe, then the append): about a millisecond on SQLite
# and a few on Postgres. Losing 256 in a row takes a log that grew by a record in each
# of 256 round trips, a quarter of a second to a few seconds of out-writing every
# read: no burst, but a writer outrunning its reporter, where more attempts change
# nothing and the caller had better learn that its report has not landed.
REPORT_ATTEMPTS = 256


class ReportStarvedError(RuntimeError):
    """A death report lost its CAS ``attempts`` times in a row (``REPORT_ATTEMPTS``):
    the log grew faster than the report could read it, and nothing was appended.
    It names the claim; a Channel carries no run identity, so the caller that knows
    the run adds it (``LocalLauncher.reap`` notes it on every failure)."""

    def __init__(self, claim_seq: int, attempts: int) -> None:
        super().__init__(
            f"the death report of claim {claim_seq} lost its CAS {attempts} times "
            "in a row: the log grows faster than the report reads it, and nothing "
            "was appended"
        )
        self.claim_seq = claim_seq
        self.attempts = attempts


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
    - ``None`` when the claim has a readable exit: some readable
      ``lifecycle.stopped`` names it. Its self-report outranks every report (§5).
      An unreadable exit is no self-report, so a report still lands;
    - ``None`` for a ``lost`` report on a claim that has ended, which some
      ``stopped`` or ``died`` naming it, readable or not, says: evidence without a
      cause reports only while the claim is held;
    - ``ClaimAliveError`` when the claim's handle probes alive, unless ``force``.
      A dead probe, or one that cannot tell (another host), does not refuse, nor
      does an unreadable claim, which has no handle to probe.

    Its tests are presence-based and never raise (§3, "The helpers"), as the
    answer fold's ``ended`` is (§5): a contradiction in the claim's window, two
    readable exits say, is the verdict's to raise, never a reporter's, so a death
    report lands on any log.

    A claim a death report already ended is reported again, so each holder of
    evidence records it once (review §12, item 14). ``t`` is this call's clock, when
    its caller saw the death. The append is a CAS at W, and a lost CAS reads again,
    so each rule is checked at the W of the append. A report that loses
    ``REPORT_ATTEMPTS`` CASes in a row raises ``ReportStarvedError``."""
    return _report(
        ch,
        Died(claim_seq=claim_seq, outcome=outcome, evidence=evidence, t=time.time()),
        force=force,
    )


def _report(ch: Channel, died: Died, *, force: bool) -> int | None:
    """``report_death`` of a body already built (and so already checked): a reap
    dates its report by the death it reaped, the ``t`` of its ``terminated``."""
    for _ in range(REPORT_ATTEMPTS):
        w = ch.last_seq()
        claim = _claim_at(ch, died.claim_seq, w)
        ended = endings(ch, claim, w)
        if has_exit(ended) or (ended and died.outcome == Outcome.LOST):
            return None
        if not force:
            started = readable(Started, claim)
            if started is not None and resolve(started.handle) is True:
                raise ClaimAliveError(died.claim_seq, started.handle)
        seq = ch.send(asdict(died), topic=Died.TOPIC, expected_seq=w)
        if seq is not None:
            return seq
    raise ReportStarvedError(died.claim_seq, REPORT_ATTEMPTS)


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

    Held is presence-based (``claims.endings``), so a contradictory window never
    raises here; an unreadable claim has no handle to probe, so only its session
    can report it. Staleness and a cross-host handle are never evidence (§3)."""
    claim = latest_episode(ch)
    if claim is None or endings(ch, claim, ch.last_seq()):
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
    started = readable(Started, claim)
    if started is None or resolve(started.handle) is not False:
        return None
    handle = started.handle
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


def _launch_claim(
    ch: Channel, launch_id: str, *, after: int, w: int
) -> Envelope | None:
    """The newest claim naming the launch ``launch_id``, above ``after`` and at or
    below ``w``. ``after`` is at least the log's last seq before the launch began,
    since its claimant cannot claim before it is spawned. The ``request_ids`` filter
    also admits records naming no request, so the envelope's id is compared here
    (specs/launcher-record-identity.md)."""
    named = [
        e
        for e in ch.read(
            after=after, topics=[Topic.LIFECYCLE_STARTED], request_ids=[launch_id]
        )
        if e.request_id == launch_id and e.seq <= w
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


# ----- the targets: what a launcher launches ----------------------------------------


@dataclass(frozen=True)
class ThreadTarget:
    """What ``ThreadLauncher`` runs: ``fn(channel, *args, **kwargs)`` on a thread.

    ``args`` and ``kwargs`` are required: they are the worker's own arguments, and
    so change its result. The record is a value: ``args`` is a tuple, and
    ``kwargs`` a read-only copy, so neither the caller's later mutation nor the
    worker's reaches it. The objects it holds are the caller's, unfrozen (a thread
    worker receives real objects).

    It is ``Drivable``: ``driven(until)`` is the copy whose ``kwargs`` carry
    ``until``, the ``Target`` the Worker's ``steps(until)`` takes."""

    fn: Callable[..., object]
    _: KW_ONLY
    args: tuple[object, ...]
    kwargs: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "kwargs", MappingProxyType(dict(self.kwargs)))

    def driven(self, until: Target) -> Self:
        """This target, driven to ``until``: a copy whose ``kwargs`` gain
        ``until``. ``ValueError`` when ``kwargs`` already holds one: a drive's
        target is the drive's, and replacing the caller's in silence would answer
        for it."""
        if "until" in self.kwargs:
            raise ValueError(
                "a driven target's until is the drive's: keep it out of kwargs "
                f"(got until={self.kwargs['until']!r})"
            )
        return replace(self, kwargs={**self.kwargs, "until": until})


@dataclass(frozen=True)
class Command:
    """What ``LocalLauncher`` runs: the subprocess ``argv``, with ``env`` merged
    over ``os.environ`` and under the launcher's ``RUNSTATE_*`` variables.

    ``env`` is required: it changes the child's result, and ``{}`` says "the
    parent's environment" explicitly. The record is a value: ``argv`` is a tuple,
    and ``env`` a read-only copy. ``argv`` is a non-empty sequence of arguments,
    never a single string, which would be split into its characters.

    Not ``Drivable``. A subprocess has no defined way to receive a ``Target``:
    0.3.0's ``launch_producer`` did not drive a ``LocalLauncher`` either, and
    ``examples/redrive`` plumbs its own. *Revival trigger:* a defined environment
    contract, e.g. the target's ``atom()`` JSON in an environment variable that
    the Worker reads; ``driven`` would then set it in ``env``."""

    argv: Sequence[str]
    _: KW_ONLY
    env: Mapping[str, str]

    def __post_init__(self) -> None:
        if isinstance(self.argv, str):
            raise TypeError(
                f"argv is a sequence of arguments, not a string: pass [{self.argv!r}]"
            )
        if not self.argv:
            raise ValueError("argv must name a program")
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


class Drivable(Protocol):
    """A target a drive can aim (``launch_producer``): ``driven(until)`` is the
    target that launches a worker whose ``steps(until)`` runs to ``until``. How
    the ``Target`` reaches the worker is the target type's: a thread's keyword
    (``ThreadTarget``); a subprocess has no contract yet (``Command``)."""

    def driven(self, until: Target) -> Self: ...


T_contra = TypeVar("T_contra", contravariant=True)
T = TypeVar("T")


class Launcher(Protocol[T_contra]):
    """Spawn a worker into a run and bracket it with ``supervisor.launched`` /
    ``supervisor.terminated``, and a ``supervisor.died`` where the launch's end is
    evidence of its claimant's. ``create_channel`` births the run (the launch);
    ``attach_channel`` reads an existing one (the deciders, which treat
    ``RunNotFound`` as "not launched yet").

    A Strategy typed by its target record: ``ThreadLauncher`` is a
    ``Launcher[ThreadTarget]`` and ``LocalLauncher`` a ``Launcher[Command]``. The
    caller resolves the launcher where it reads its configuration, builds the
    target record that launcher takes, and passes both down; a decider, a producer
    or a sweep that takes ``Launcher[T]`` and a ``T`` cannot be handed a target its
    launcher does not run (``_proof`` below). Contravariant: a launcher of a wider
    target type launches any narrower one."""

    def create_channel(self, run_id: str) -> Channel: ...
    def attach_channel(self, run_id: str) -> Channel: ...
    def launch(self, run_id: str, target: T_contra) -> LaunchHandle: ...


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

    ``backend`` defaults to the memory backend, and ``root`` to none, which is the
    memory backend's: both say where a log lives, not what it holds, so their
    defaults change no result (plan, "Exempt defaults")."""

    def __init__(
        self, *, root: str | os.PathLike[str] | None = None, backend: str = "memory"
    ) -> None:
        self._root = root
        self._backend = backend

    def create_channel(self, run_id: str) -> Channel:
        return create_channel(run_id, root=self._root, backend=self._backend)

    def attach_channel(self, run_id: str) -> Channel:
        return attach_channel(run_id, root=self._root, backend=self._backend)

    def launch(self, run_id: str, target: ThreadTarget) -> _ThreadHandle:
        """Run ``target.fn(channel, *target.args, **target.kwargs)`` on a thread
        for ``run_id``, bracketed by ``supervisor.launched`` and
        ``supervisor.terminated``, both naming the launch. ``launch_scope`` binds
        the launch id for the target, so the claim its Worker writes names the
        launch.

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
        channel = self.create_channel(run_id)
        handle = local_handle()
        launch_id = new_launch_id()
        floor = channel.last_seq()
        _launched(channel, handle, launch_id)
        state = _ThreadState()

        def _ended(exit_code: int, outcome: Outcome, evidence: str) -> None:
            """The thread's records. A write that raises is not retried, unlike
            a ``LocalLauncher`` reap's: it reaches ``threading.excepthook``. The
            launcher keeps no handles and has no ``reap()`` to call again, and the
            thread's end is observed once, by the thread itself, so there is no
            later call to retry from; and its channel is this process's own
            (memory by default), with no connection to drop."""
            t = time.time()
            end = Terminated(reason=Reap.EXITED, exit_code=exit_code, signal=None, t=t)
            channel.send(asdict(end), topic=Terminated.TOPIC, request_id=launch_id)
            w = channel.last_seq()
            claim = _launch_claim(channel, launch_id, after=floor, w=w)
            if claim is not None:
                died = Died(
                    claim_seq=claim.seq, outcome=outcome, evidence=evidence, t=t
                )
                _report(channel, died, force=True)

        def _run() -> None:
            try:
                with launch_scope(launch_id):
                    target.fn(channel, *target.args, **target.kwargs)
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


class _Step(Enum):
    """What of a reap has landed: nothing (``PENDING``), its ``terminated``
    (``ENDED``), or every record it writes (``DONE``)."""

    PENDING = "pending"
    ENDED = "ended"
    DONE = "done"


@dataclass(frozen=True)
class _Reaped:
    """What a reap established, bound once, under the handle's lock, when the
    death is first observed: ``t``, the reap's one time (observer-clock §3), on
    every record it writes; the ``Popen``'s ``returncode``; and ``by_launcher``,
    whether the death is the launcher's own orphan kill, a ``SIGKILL`` after it
    sent one, and so no evidence of how the claim ended (§3)."""

    t: float
    returncode: int
    by_launcher: bool


@dataclass(eq=False)
class _LocalHandle:
    """The handle ``LocalLauncher`` returns. It holds the child's ``Popen``, so its
    liveness, its reap and a kill go through it, with no pid-reuse window; the
    portable ``handle`` string is for other observers, which hold no ``Popen``.

    Its reap's state is held here, under ``_lock``, since ``waitpid`` yields the
    exit status once and a ``wait()`` and a ``poll()`` may race on two threads:
    ``_death``, what the reap established, bound once; ``_step``, what of it has
    landed, so a write that failed is retried by the next ``poll`` or ``wait`` and a
    record that landed is not written again; and ``_kill_sent``, latched once the
    launcher has sent its orphan kill, never cleared.

    ``channel`` is the handle's own, which the launcher opened for it and its
    ``reap()`` closes once the reap is done. A channel never reconnects
    (specs/channel-postgres.md), so once it has raised, the reap's next use opens
    a fresh one for the run (``_reopen``)."""

    run_id: str
    channel: Channel
    handle: str
    _proc: subprocess.Popen[bytes]
    launch_id: str
    _floor: int  # the log's last seq before the spawn: the claim lands above it
    _reopen: Callable[[], Channel]  # attaches a fresh channel to this run
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)
    _death: _Reaped | None = field(init=False, default=None)
    _step: _Step = field(init=False, default=_Step.PENDING)
    _kill_sent: bool = field(init=False, default=False)
    _failed: bool = field(init=False, default=False)  # ``channel`` has raised
    # The newest claim naming the launch, read up to ``_claim_read``.
    _claim: Envelope | None = field(init=False, default=None)
    _claim_read: int = field(init=False)

    def __post_init__(self) -> None:
        self._claim_read = self._floor

    def is_alive(self) -> bool:
        """Pure for the log: it reads the ``Popen``, and writes nothing, even when
        the child has exited; the reap of ``wait`` or ``poll`` writes. When the
        ``Popen`` reaps the exited child here, the death's ``t`` is bound
        (``_death``), so the records a later reap writes are dated by when it was
        first seen."""
        if self._proc.poll() is None:
            return True
        with self._lock:
            self._observed()
        return False

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
        """Send SIGTERM, the caller's request to stop. The reap, and its records,
        follow on ``wait`` or ``poll``."""
        self._proc.terminate()

    def _kill(self) -> None:
        """Kill this child as an orphan (``SIGKILL``; §3, "A session probe"): a
        graceful window buys the log nothing, since its session is gone and a
        released worker is fenced, and a child that handles ``SIGTERM``, as a
        requeue handler does, would live beside its successor.

        ``_kill_sent`` is latched before the signal and never cleared, so a later
        cycle's kill of a child still dying from the first cannot unmark it. Its
        reap is the launcher's exactly when ``_kill_sent`` and the status is a
        ``SIGKILL`` (``_observed``): a child that exits on its own between
        ``Popen``'s check and the signal keeps its own status, and is reaped as
        its own end. A child already reaped is sent nothing, and its end is its
        own."""
        with self._lock:
            if self._proc.returncode is not None:
                return
            self._kill_sent = True
            self._proc.kill()

    def _observed(self) -> _Reaped | None:
        """Under the lock: what the reap established, bound the first time the
        ``Popen`` holds the exit status; ``None`` while the child runs."""
        rc = self._proc.returncode
        if self._death is None and rc is not None:
            by_launcher = self._kill_sent and rc == -signal.SIGKILL
            self._death = _Reaped(t=time.time(), returncode=rc, by_launcher=by_launcher)
        return self._death

    @contextmanager
    def _io(self) -> Iterator[Channel]:
        """Under the lock: the channel the reap reads and writes on. Once
        ``channel`` has raised, a fresh one is opened for the run, and the old one
        closed: a channel never reconnects, so every retry on it would raise
        forever. A fresh channel is correct for every record here: a
        ``terminated`` names the launch, and a ``died`` is a third party's, never
        the claim's own session's (specs/channel-postgres.md)."""
        if self._failed:
            old, self.channel = self.channel, self._reopen()
            self._failed = False
            old.close()
        try:
            yield self.channel
        except Exception:
            self._failed = True
            raise

    def _own_claim(self, ch: Channel) -> Envelope | None:
        """Under the lock: the newest claim naming this launch, if its claimant is
        this child: its handle is the launch's whole ``local://host/pid``
        (``local_handle_of``, the one build site of both), compared as the
        migration compares it (§7). A wrapper's grandchild that claims, as under
        ``sbatch`` or ``sh -c``, is not: the child's end says nothing of it. Nor is
        a claim whose ``started`` is unreadable, which names no handle: it
        establishes no claimant. It never raises on what the log holds.

        The newest claim found is cached (``_claim``), and each call reads only
        above what the last read, so it stays the newest while each read stays
        bounded by the records since."""
        w = ch.last_seq()
        newer = _launch_claim(ch, self.launch_id, after=self._claim_read, w=w)
        if newer is not None:
            self._claim = newer
        self._claim_read = max(self._claim_read, w)
        started = None if self._claim is None else readable(Started, self._claim)
        if started is None or started.handle != self.handle:
            return None
        return self._claim

    def _orphaned(self) -> bool:
        """Is this child an orphan (§3, "A session probe")? It is the claimant
        (``_own_claim``) of a claim a ``supervisor.died`` names, readable or not,
        and no ``lifecycle.stopped`` does, readable or not: its session may be gone
        while it lives, so it can write nothing more, and a successor would run
        beside it. A claim its own exit ended is finishing, not orphaned. Read by
        presence, so it never raises on what the log holds."""
        with self._lock, self._io() as ch:
            claim = self._own_claim(ch)
            if claim is None:
                return False
            topics = {e.topic for e in endings(ch, claim, ch.last_seq())}
        return Topic.SUPERVISOR_DIED in topics and Topic.LIFECYCLE_STOPPED not in topics

    def _reap(self) -> None:
        """Record this child's end, exactly once, under the lock: its
        ``terminated``, unconditionally, since a launcher reports what its own
        child did and the launch id says whose end it is; then, when the child is
        the claimant (``_own_claim``), its claim has no readable exit, and its
        death is not the launcher's orphan kill (``_Reaped.by_launcher``), a death
        report, the only place this launcher writes one (§3, "The writers"):

        - a signal is ``killed`` (``reap: signal <s>``) and a nonzero exit
          ``errored`` (``reap: exit <n>``), whether or not the claim has ended;
        - an exit 0 is ``lost`` (``reap: exit 0``), only while the claim is held: a
          worker a mistaken report released exits 0, and adds nothing to it.

        The reap of a child killed as an orphan writes ``terminated`` only: its
        signal is the launcher's, sent because the claim had already ended, so it
        is no evidence of how the claim ended (§3).

        It passes ``force=True``: ``waitpid`` is definitive, and the freed pid may
        already be reused. One reap, one ``t`` (``_death``), on both records. A
        write that raises leaves ``_step`` where it was, so the next call retries
        what has not landed, on a fresh channel (``_io``)."""
        with self._lock:
            death = self._observed()
            assert death is not None  # called once the Popen holds the status
            if self._step is _Step.DONE:
                return
            rc, t = death.returncode, death.t
            if rc < 0:  # died from signal -rc
                end = Terminated(reason=Reap.KILLED, exit_code=None, signal=-rc, t=t)
                outcome, evidence = Outcome.KILLED, f"reap: signal {-rc}"
            else:
                end = Terminated(reason=Reap.EXITED, exit_code=rc, signal=None, t=t)
                outcome = Outcome.ERRORED if rc else Outcome.LOST
                evidence = f"reap: exit {rc}"
            with self._io() as ch:
                if self._step is _Step.PENDING:
                    ch.send(
                        asdict(end), topic=Terminated.TOPIC, request_id=self.launch_id
                    )
                    self._step = _Step.ENDED
                claim = None if death.by_launcher else self._own_claim(ch)
                if claim is not None:
                    died = Died(
                        claim_seq=claim.seq, outcome=outcome, evidence=evidence, t=t
                    )
                    _report(ch, died, force=True)
                self._step = _Step.DONE

    def _done(self) -> bool:
        """Has the reap landed every record it writes?"""
        with self._lock:
            return self._step is _Step.DONE

    def _close(self) -> None:
        """Close the handle's channel: its reap is done, and nothing writes on it
        again."""
        with self._lock:
            self.channel.close()


def _named(exc: Exception, h: _LocalHandle) -> Exception:
    """``exc``, noted with the handle it failed on."""
    exc.add_note(f"run {h.run_id!r}, launch {h.launch_id}, {h.handle}")
    return exc


class LocalLauncher:
    """Spawn workers as local subprocesses.

    ``launch`` runs a command with ``RUNSTATE_*`` in its environment; the child
    calls ``runstate.current_channel()`` to open the same run's channel, so the
    backend must be shared across processes (sqlite by default: ``backend`` says
    where a log lives, not what it holds, so its default changes no result). As a
    context manager it reaps on exit (``reap``), and does not block on stragglers.

    The limits of its orphan kill (§3, "A session probe"):

    - a standing loop calls ``reap()`` before its decider, and tolerates one cycle
      in which the successor runs beside an orphan that can no longer write: the
      report that ends the claim may land after this cycle's reap;
    - a wrapper's grandchild (``sh -c``, ``sbatch``) is no child of this
      launcher's, and its claim is not the child's (``_own_claim``), so it kills
      nothing there: that is the wrapper's to do, or a process group's.
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

    def launch(self, run_id: str, target: Command) -> _LocalHandle:
        """Spawn ``target.argv`` for ``run_id``, its environment ``os.environ``,
        then ``target.env``, then the ``RUNSTATE_*`` variables that open the run's
        channel and name the launch, each over the last."""
        channel = self.create_channel(run_id)
        launch_id = new_launch_id()
        floor = channel.last_seq()
        child_env = {
            **os.environ,
            **target.env,
            "RUNSTATE_RUN_ID": run_id,
            "RUNSTATE_CHANNEL_ROOT": str(self._root),
            "RUNSTATE_CHANNEL_BACKEND": self._backend,
            # The child's claim re-emits it, so it names the launch that spawned it.
            LAUNCH_ID_ENV: launch_id,
        }
        proc = subprocess.Popen(list(target.argv), env=child_env)
        handle = local_handle_of(proc.pid)
        _launched(channel, handle, launch_id)
        h = _LocalHandle(
            run_id=run_id,
            channel=channel,
            handle=handle,
            _proc=proc,
            launch_id=launch_id,
            _floor=floor,
            # the run exists: the launch birthed it
            _reopen=functools.partial(self.attach_channel, run_id),
        )
        self._handles.append(h)
        return h

    def reap(self) -> None:
        """Reap every finished child, each writing its records at most once, and
        kill (``SIGKILL``), through its ``Popen``, a live child of its own that is
        an orphan (``_orphaned``): its claim a death report ended, its session
        perhaps gone, and a successor would otherwise run beside it (§3, "A
        session probe"). Only this kills, and never by pid, which pid reuse makes
        unsafe (§12; plan, open question 6); the deciders never kill. The child's
        reap, on a later call, writes its ``terminated`` only.

        A handle whose reap is done is dropped, and its channel closed, so a
        standing loop holds a channel per live child, not per launch.

        Each handle is processed on its own: a run's backend that raises does not
        keep another child from being reaped. The errors, each noted with the run,
        the launch and the handle it failed on, are raised together, as an
        ``ExceptionGroup``, once every handle has been processed, and a reap a
        failed write left undone is retried on the next call, on a fresh channel.
        What the log holds never raises here: the tests are presence-based (§3,
        "The helpers").

        Load-bearing for a standing waker loop (specs/lazy-launch.md): an unreaped
        child is a zombie, its pid probes alive, and its death goes unreported."""
        errors: list[Exception] = []
        kept: list[_LocalHandle] = []
        for h in self._handles:
            try:
                if h.poll() is None and h._orphaned():
                    h._kill()
            except Exception as exc:  # raised below, once every handle is done
                errors.append(_named(exc, h))
            if not h._done():
                kept.append(h)
                continue
            try:
                h._close()
            except Exception as exc:  # its records landed: nothing to retry
                errors.append(_named(exc, h))
        self._handles = kept
        if errors:
            raise ExceptionGroup("LocalLauncher.reap: some handles failed", errors)

    def __enter__(self) -> LocalLauncher:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Reap (``reap``), without blocking on stragglers.

        When the body did not raise, the reap's ``ExceptionGroup`` propagates. When
        it did, the body's exception propagates, and each reap failure is attached
        to it as a note: raising the group would replace the cause with its
        consequence (the reap often fails because of what made the body fail), and
        a log line may land where nobody reads, while a note travels with the
        traceback the caller sees."""
        if exc is None:
            self.reap()
            return
        try:
            self.reap()
        except ExceptionGroup as group:
            for e in group.exceptions:
                where = "; ".join(e.__notes__)
                exc.add_note(f"LocalLauncher.reap also failed ({where}): {e!r}")


# ----- the deciders (§3, §5) -------------------------------------------------------


def relaunch_if_needed(
    launcher: Launcher[T], run_id: str, target: T
) -> LaunchHandle | None:
    """Launch ``target`` into ``run_id`` unless its newest claim is held (§5): the
    durable-demand decider. A held claim is first given to ``report_if_dead``
    (§3, "The writers"): when its report lands, the claim has ended and it
    launches; when nothing reports, it returns ``None``. It never kills.

    A best-effort single-spawn guard over a log read: correctness rests on the
    worker's claim CAS, and a check-to-spawn race wastes a spawn. ``target`` is
    the record ``launcher`` takes (``Launcher[T]``), so a mismatched pair is a
    type error, never a launch-time one."""
    try:
        with launcher.attach_channel(run_id) as channel:  # a probe handle: close it
            if live_episode(channel) is not None and report_if_dead(channel) is None:
                return None
    except RunNotFound:
        pass  # no records: nothing claimed, so nothing is held
    return launcher.launch(run_id, target)


def ensure_served(
    launcher: Launcher[T], run_id: str, target: T, *, demand: LiveDemand
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
    return launcher.launch(run_id, target)


# ----- the proof: each reference launcher satisfies the Protocol -------------------

if TYPE_CHECKING:

    def _proof(
        thread: ThreadLauncher, local: LocalLauncher, target: ThreadTarget
    ) -> tuple[Launcher[ThreadTarget], Launcher[Command], Drivable]:
        """Checked by ``mypy --strict runstate``, never run: it fails when a
        reference launcher stops satisfying ``Launcher`` of its target record, or
        ``ThreadTarget`` stops being ``Drivable``."""
        return thread, local, target
