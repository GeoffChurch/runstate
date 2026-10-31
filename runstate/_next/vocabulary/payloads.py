"""The typed bodies of log format 0.4.0 (docs/specs/commits-and-lineage.md §3).

The flat, fixed-shape bodies of the ``lifecycle.*`` (lifecycle-v0.6),
``supervisor.*`` (supervisor-v0.5), ``value`` (value-v0.3) and ``control.withdraw``
(subscription-v0.4) records, as **frozen dataclasses**: the Python mirror of the
JSON schema stack, which stays the wire-authoritative, language-neutral source of
truth. A body serializes with ``dataclasses.asdict`` at ``channel.send`` (a
``StrEnum`` field serializes as its string) and parses back with
``Cls(**envelope.body)``.

Each body validates in ``__post_init__`` exactly what its schema definition
admits, so ``Cls(**body)`` raises (``TypeError`` for a missing or an extra key,
``ValueError`` for a bad value) if and only if the schema refuses the body, with
two exceptions, where JSON Schema admits what these refuse: a whole-number float
such as ``3.0`` is no integer here, as in the migration step, and NaN is no number,
since it compares false with every bound (``minimum: 0`` admits it). No writer of a
0.4.0 log writes either: Python's ``json`` writes an int as an int, and a clock is
never NaN. ``bool`` is never a number either:
JSON ``true`` is not a seq and not a clock. A record's envelope (its ``name`` and
``request_id``) is the convention schema's, not the body's.

The schedules of ``control.subscribe`` and ``control.stop`` are not here: they are
the condition algebra, kept as the wire dict, in ``schedule.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Literal, Protocol, TypeGuard, TypeVar


class Topic(StrEnum):
    """The closed, protocol-owned routing keys of log format 0.4.0
    (``Envelope.topic``): every topic the 0.4.0 convention schemas pin, the
    body-less ones included. Each member IS its wire string, so it serializes
    byte-identically and a read-back plain-str topic compares equal. A body's
    ``TOPIC`` is the member of its topic. The ``name`` axis stays open and
    application-owned.

    Against 0.3.0 (§3): ``lifecycle.commit``, ``lifecycle.rewind``,
    ``supervisor.died`` and ``control.withdraw`` are new; ``launcher.*`` is renamed
    ``supervisor.*``; ``control.unsubscribe`` is retired."""

    VALUE = "value"
    LIFECYCLE_STARTED = "lifecycle.started"
    LIFECYCLE_COMMIT = "lifecycle.commit"
    LIFECYCLE_HEARTBEAT = "lifecycle.heartbeat"
    LIFECYCLE_REWIND = "lifecycle.rewind"
    LIFECYCLE_STOPPED = "lifecycle.stopped"
    LIFECYCLE_BOUND = "lifecycle.bound"
    LIFECYCLE_NAK = "lifecycle.nak"
    SUPERVISOR_LAUNCHED = "supervisor.launched"
    SUPERVISOR_TERMINATED = "supervisor.terminated"
    SUPERVISOR_DIED = "supervisor.died"
    CONTROL_SUBSCRIBE = "control.subscribe"
    CONTROL_STOP = "control.stop"
    CONTROL_WITHDRAW = "control.withdraw"


class Outcome(StrEnum):
    """How a claim or a run ended (§5): the closed codomain of a verdict's outcomes.
    Here, not in ``observables``, because a wire field carries it: a death report's
    ``Died.outcome`` (plan, open question 2). ``lost`` is new in 0.4.0: its writer
    saw the claim end without an exit, and nothing of how (§3, supervisor-v0.5).
    ``presumed_dead`` is the Watcher's inference, which no record carries."""

    COMPLETED = "completed"
    PREEMPTED = "preempted"
    ERRORED = "errored"
    KILLED = "killed"
    LOST = "lost"
    PRESUMED_DEAD = "presumed_dead"

    @classmethod
    def failures(cls) -> frozenset[Outcome]:
        """The outcomes in which the worker died, rather than finished or was
        preempted cleanly: what ``sweep`` and ``ensure`` stop on."""
        return frozenset({cls.ERRORED, cls.KILLED, cls.LOST, cls.PRESUMED_DEAD})

    @classmethod
    def deaths(cls) -> frozenset[Outcome]:
        """A death report's outcomes (supervisor-v0.5's ``Died.outcome``): a death
        is never a completion, and a report carries evidence, never a presumption."""
        return frozenset({cls.PREEMPTED, cls.ERRORED, cls.KILLED, cls.LOST})


class NakReason(StrEnum):
    """lifecycle-v0.6's ``Nak.reason``: why the worker refused a control request."""

    MALFORMED = "malformed"
    UNSATISFIABLE = "unsatisfiable"
    UNSUPPORTED = "unsupported"


class Reap(StrEnum):
    """supervisor-v0.5's ``Terminated.reason``: how a launch's process ended."""

    EXITED = "exited"
    KILLED = "killed"


# ----- the checks, one per shared schema definition ---------------------------------


_E = TypeVar("_E", bound=StrEnum)

# The members each enum field admits, computed once.
_NAK_REASONS = frozenset(NakReason)
_REAPS = frozenset(Reap)
_DEATHS = Outcome.deaths()


def _member(field: str, cls: type[_E], v: object, allowed: frozenset[_E]) -> _E:
    """An enum field: one of ``allowed``, parsed from its wire string (or given as
    the member), and returned as the member, so the frozen body holds the enum."""
    if not (isinstance(v, str) and v in allowed):
        raise ValueError(
            f"{field} must be one of {sorted(map(str, allowed))}, got {v!r}"
        )
    return cls(v)


def _is_int(v: object) -> TypeGuard[int]:
    return isinstance(v, int) and not isinstance(v, bool)


def _seq(field: str, v: object, *, nullable: bool) -> None:
    """``Seq``, or ``Parent`` when nullable: an integer >= 1."""
    if v is None and nullable:
        return
    if not (_is_int(v) and v >= 1):
        raise ValueError(f"{field} must be a seq (an integer >= 1), got {v!r}")


def _watermark(field: str, v: object) -> None:
    """``consumed_seq``: an integer >= 0, the control-drain watermark."""
    if not (_is_int(v) and v >= 0):
        raise ValueError(f"{field} must be an integer >= 0, got {v!r}")


def _time(field: str, v: object, *, nullable: bool) -> None:
    """``Time``: a number >= 0. Spelled ``v >= 0`` so that NaN, which compares false
    with everything, is refused."""
    if v is None and nullable:
        return
    if not (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0):
        raise ValueError(f"{field} must be a number >= 0, got {v!r}")


def _seqs(field: str, v: object) -> None:
    """``Values``: unique seqs, order significant."""
    if not isinstance(v, list):
        raise ValueError(f"{field} must be a list of seqs, got {v!r}")
    for s in v:
        _seq(field, s, nullable=False)
    if len(set(v)) != len(v):
        raise ValueError(f"{field} must be unique, got {v!r}")


def _names(field: str, v: object) -> None:
    """``Requests``: unique, non-empty strings."""
    if not (isinstance(v, list) and all(isinstance(r, str) and r for r in v)):
        raise ValueError(f"{field} must be a list of non-empty strings, got {v!r}")
    if len(set(v)) != len(v):
        raise ValueError(f"{field} must be unique, got {v!r}")


def _string(field: str, v: object) -> None:
    """A JSON string, possibly empty."""
    if not isinstance(v, str):
        raise ValueError(f"{field} must be a string, got {v!r}")


# ----- value-v0.3 -------------------------------------------------------------------


@dataclass(frozen=True)
class Value:
    """value-v0.3's body: a value of the stream or source the envelope ``name``
    names. ``step`` is removed: a value belongs to the node whose ``values`` names
    its seq (§2). ``t`` is the writer's wall clock, or null when unstamped."""

    value: Any
    t: float | None
    TOPIC: ClassVar[Topic] = Topic.VALUE

    def __post_init__(self) -> None:
        _time("t", self.t, nullable=True)


# ----- lifecycle-v0.6 ---------------------------------------------------------------


@dataclass(frozen=True)
class Started:
    """The claim (§2, "Position"): who computes (``handle``, the worker's
    self-reported liveness handle), when (``t``, the run epoch), where it starts
    (``resume_from``: an extendable node of this run, or null for no node of this
    run) and which names it exposes as sources, sampled on demand (``sources``;
    ``[]`` declares none)."""

    handle: str
    t: float
    resume_from: int | None
    sources: list[str]
    TOPIC: ClassVar[Topic] = Topic.LIFECYCLE_STARTED

    def __post_init__(self) -> None:
        _string("handle", self.handle)
        _time("t", self.t, nullable=False)
        _seq("resume_from", self.resume_from, nullable=True)
        _names("sources", self.sources)


class Node(Protocol):
    """A lineage node (§3, "One shared type"): a ``Commit`` or a ``Stopped``, the
    exit node. Lineage walks take a ``Node``. Its members are read-only, as a frozen
    dataclass's fields are.

    Its kind follows from its body alone (§2, "Nodes"): **closed** if and only if
    it is a completed exit; **torn** if and only if it is an exit that did not
    complete and discarded its open iteration (``values: null``); **extendable**
    otherwise, a commit or an exit that did not complete and committed its pending
    values."""

    @property
    def claim_seq(self) -> int: ...

    @property
    def parent(self) -> int | None: ...

    @property
    def fired(self) -> list[str]: ...

    @property
    def expired(self) -> list[str]: ...

    @property
    def committed(self) -> list[int]:
        """The value seqs it commits, in its own order: none for a discarded exit."""
        ...

    @property
    def iterations(self) -> int:
        """What it adds to ``Commits()``: 1 for a commit, 0 for an exit (§2)."""
        ...

    @property
    def extendable(self) -> bool: ...

    @property
    def closed(self) -> bool: ...

    @property
    def t(self) -> float:
        """Its writer's clock when it landed, which a replay's time atoms read
        (§5, ``history``)."""
        ...


@dataclass(frozen=True)
class Commit:
    """A lineage node, one per ``commit()`` (§3): ``claim_seq`` the claim it speaks
    for; ``consumed_seq`` the control-drain watermark; ``parent`` the claim's
    position before it; ``values`` the seqs it commits, in the writer's order;
    ``fired`` the subscriptions that fired here; ``expired`` those whose schedule
    ended here. Always extendable."""

    claim_seq: int
    consumed_seq: int
    parent: int | None
    values: list[int]
    fired: list[str]
    expired: list[str]
    t: float
    TOPIC: ClassVar[Topic] = Topic.LIFECYCLE_COMMIT

    def __post_init__(self) -> None:
        _seq("claim_seq", self.claim_seq, nullable=False)
        _watermark("consumed_seq", self.consumed_seq)
        _seq("parent", self.parent, nullable=True)
        _seqs("values", self.values)
        _names("fired", self.fired)
        _names("expired", self.expired)
        _time("t", self.t, nullable=False)

    @property
    def committed(self) -> list[int]:
        return self.values

    @property
    def iterations(self) -> int:
        return 1

    @property
    def extendable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        return False


@dataclass(frozen=True)
class Heartbeat:
    """Liveness only, not a node (§3): ``step`` is removed. ``claim_seq`` names the
    claim the beat speaks for; ``consumed_seq`` is the control-drain watermark."""

    claim_seq: int
    consumed_seq: int
    t: float
    TOPIC: ClassVar[Topic] = Topic.LIFECYCLE_HEARTBEAT

    def __post_init__(self) -> None:
        _seq("claim_seq", self.claim_seq, nullable=False)
        _watermark("consumed_seq", self.consumed_seq)
        _time("t", self.t, nullable=False)


@dataclass(frozen=True)
class Rewind:
    """Restates the claim's position, never who (§2): ``resume_from`` is an
    extendable node of this run, or null for the root."""

    claim_seq: int
    resume_from: int | None
    t: float
    TOPIC: ClassVar[Topic] = Topic.LIFECYCLE_REWIND

    def __post_init__(self) -> None:
        _seq("claim_seq", self.claim_seq, nullable=False)
        _seq("resume_from", self.resume_from, nullable=True)
        _time("t", self.t, nullable=False)


@dataclass(frozen=True)
class Stopped:
    """The worker's exit, its claim's last node (§3, §4 "The exit"); at most one per
    claim. ``completed`` is the worker's claim of intrinsic completion, a closed
    node. ``values`` is null when the exit discarded its open iteration, else the
    pending values then the samples. ``honored`` names the stops it honored.

    The schema's three ``if``/``then``s: a completed exit carries no error; an
    error exit discards its iteration (``values`` null); and an exit that discarded
    its iteration evaluates nothing (``fired`` and ``expired`` empty)."""

    completed: bool
    error: str | None
    claim_seq: int
    honored: list[str]
    parent: int | None
    values: list[int] | None
    fired: list[str]
    expired: list[str]
    t: float
    TOPIC: ClassVar[Topic] = Topic.LIFECYCLE_STOPPED

    def __post_init__(self) -> None:
        if not isinstance(self.completed, bool):
            raise ValueError(f"completed must be a bool, got {self.completed!r}")
        if not (self.error is None or isinstance(self.error, str)):
            raise ValueError(f"error must be a string or null, got {self.error!r}")
        _seq("claim_seq", self.claim_seq, nullable=False)
        _names("honored", self.honored)
        _seq("parent", self.parent, nullable=True)
        if self.values is not None:
            _seqs("values", self.values)
        _names("fired", self.fired)
        _names("expired", self.expired)
        _time("t", self.t, nullable=False)
        if self.completed and self.error is not None:
            raise ValueError("a completed exit carries no error")
        if self.error is not None and self.values is not None:
            raise ValueError("an error exit discards its iteration: values is null")
        if self.values is None and (self.fired or self.expired):
            raise ValueError("an exit that discarded its iteration evaluates nothing")

    @property
    def committed(self) -> list[int]:
        return [] if self.values is None else self.values

    @property
    def iterations(self) -> int:
        return 0

    @property
    def extendable(self) -> bool:
        return not self.completed and self.values is not None

    @property
    def closed(self) -> bool:
        return self.completed


@dataclass(frozen=True)
class Bound:
    """A lease, named by the envelope ``request_id``, bound to the claim
    ``claim_seq`` names (§2, "Per claim"); void once that claim has ended.
    Unchanged from lifecycle-v0.5."""

    claim_seq: int
    TOPIC: ClassVar[Topic] = Topic.LIFECYCLE_BOUND

    def __post_init__(self) -> None:
        _seq("claim_seq", self.claim_seq, nullable=False)


@dataclass(frozen=True)
class Nak:
    """A refused control request, named by the envelope ``request_id``, which it
    spends (§2, "Requests"). ``message`` is free human detail. Unchanged from
    lifecycle-v0.5."""

    reason: NakReason
    message: str
    TOPIC: ClassVar[Topic] = Topic.LIFECYCLE_NAK

    def __post_init__(self) -> None:
        reason = _member("reason", NakReason, self.reason, _NAK_REASONS)
        object.__setattr__(self, "reason", reason)
        _string("message", self.message)


# ----- subscription-v0.4 ------------------------------------------------------------


@dataclass(frozen=True)
class Withdraw:
    """``control.withdraw`` (§3, subscription-v0.4): the asker's cancellation of the
    request its envelope ``request_id`` names, a stop or a subscription, which it
    spends. ``t`` is the asker's wall clock."""

    t: float
    TOPIC: ClassVar[Topic] = Topic.CONTROL_WITHDRAW

    def __post_init__(self) -> None:
        _time("t", self.t, nullable=False)


# ----- supervisor-v0.5 --------------------------------------------------------------


@dataclass(frozen=True)
class Launched:
    """Spawn-intent plus the worker's liveness handle, named by the envelope
    ``request_id``, the launch id. ``status`` is always ``"running"``, required on
    the wire and so with no default here. Unchanged from launcher-v0.4."""

    handle: str
    status: Literal["running"]
    t: float
    TOPIC: ClassVar[Topic] = Topic.SUPERVISOR_LAUNCHED

    def __post_init__(self) -> None:
        _string("handle", self.handle)
        if self.status != "running":
            raise ValueError(f"status must be 'running', got {self.status!r}")
        _time("t", self.t, nullable=False)


@dataclass(frozen=True)
class Terminated:
    """How a launch's process ended, named by the envelope ``request_id``: an exit
    carries its code and no signal, a kill its signal and no code. Unchanged from
    launcher-v0.4."""

    reason: Reap
    exit_code: int | None
    signal: int | None
    t: float
    TOPIC: ClassVar[Topic] = Topic.SUPERVISOR_TERMINATED

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _member("reason", Reap, self.reason, _REAPS))
        code, signal = self.exit_code, self.signal
        if self.reason == Reap.EXITED:
            if not (_is_int(code) and code >= 0 and signal is None):
                raise ValueError(
                    "an exit carries a code >= 0 and no signal,"
                    f" got {code!r}, {signal!r}"
                )
        elif not (_is_int(signal) and signal >= 1 and code is None):
            raise ValueError(
                f"a kill carries a signal >= 1 and no code, got {signal!r}, {code!r}"
            )
        _time("t", self.t, nullable=False)


@dataclass(frozen=True)
class Died:
    """The death report (§3, supervisor-v0.5): a third party's account of how the
    claim ``claim_seq`` names ended. ``outcome`` is a death's, never a completion;
    ``evidence`` is a non-empty string no fold parses, prefixed by its kind
    (``reap:``, ``probe:``, ``session:``, ``slurm:`` ...), and the one place a
    report says who wrote it. ``t`` is the reporter's clock when it saw the death."""

    claim_seq: int
    outcome: Outcome
    evidence: str
    t: float
    TOPIC: ClassVar[Topic] = Topic.SUPERVISOR_DIED

    def __post_init__(self) -> None:
        _seq("claim_seq", self.claim_seq, nullable=False)
        outcome = _member("outcome", Outcome, self.outcome, _DEATHS)
        object.__setattr__(self, "outcome", outcome)
        if not (isinstance(self.evidence, str) and self.evidence):
            raise ValueError(
                f"evidence must be a non-empty string, got {self.evidence!r}"
            )
        _time("t", self.t, nullable=False)
