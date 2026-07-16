"""Typed convention bodies (docs/design-v0.2.md §6-8).

The flat, fixed-shape bodies of the ``lifecycle.*`` / ``launcher.*`` / ``value``
conventions, as **frozen dataclasses** -- the Python mirror of the JSON schema
stack (which stays the wire-authoritative, language-neutral source of truth).

The substrate is body-agnostic, so these are convention-layer helpers: serialize
to a plain dict (``dataclasses.asdict``) at the ``channel.send`` boundary, and
parse back with ``Cls(**envelope.body)``. A worker that opts out of the
conventions just composes a dict directly.

The recursive subscription schedule (``control.subscribe``) is deliberately NOT
here -- it is a condition algebra, modelled in ``schedule.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Optional


class Topic(StrEnum):
    """The CLOSED, protocol-owned routing keys (``Envelope.topic``) — the complete
    enumerable set, including the body-less ``control.*`` verbs. StrEnum: each member
    IS its wire string (``Topic.VALUE == "value"``), so it serializes byte-identically
    and a read-back plain-str topic compares equal — zero channel migration. The
    ``name`` axis stays open/app-owned (the consumer's concern, not the protocol's).
    Each spelling is defined exactly once — here, on the ``Topic`` member; a
    body-bearing topic's ``<Payload>.TOPIC`` is a typed alias of that same member
    (``Stopped.TOPIC is Topic.LIFECYCLE_STOPPED``). Every internal emit/read site
    routes on ``Topic.X`` / ``<Payload>.TOPIC``, never a bare literal."""
    VALUE = "value"
    LIFECYCLE_STARTED = "lifecycle.started"
    LIFECYCLE_HEARTBEAT = "lifecycle.heartbeat"
    LIFECYCLE_STOPPED = "lifecycle.stopped"
    LIFECYCLE_NAK = "lifecycle.nak"
    LAUNCHER_LAUNCHED = "launcher.launched"
    LAUNCHER_TERMINATED = "launcher.terminated"
    CONTROL_STOP = "control.stop"
    CONTROL_SUBSCRIBE = "control.subscribe"
    CONTROL_UNSUBSCRIBE = "control.unsubscribe"


@dataclass(frozen=True)
class Value:
    """A worker's current value for ``name``, sampled per a subscription."""

    value: Any
    step: Optional[int]  # present-nullable: null when the worker is stepless
    t: Optional[float]  # absolute wall-clock seconds (the real-time axis); null = unstamped
    TOPIC: ClassVar[str] = Topic.VALUE


@dataclass(frozen=True)
class Started:
    """Pushed on attach; the worker self-reports its liveness handle (§8).

    ``t`` is the worker's wall-clock at attach — every lifecycle event record carries
    a required ``t`` = when the worker emitted it (specs/observer-clock.md). It is also
    the run epoch (``memoizer._epoch``); it was ``attached_at`` (present-nullable) before
    the observer-clock harmonization."""

    handle: str
    t: float
    TOPIC: ClassVar[str] = Topic.LIFECYCLE_STARTED


@dataclass(frozen=True)
class Heartbeat:
    """Tick-driven liveness beacon (§7): progress + the consumption watermark.

    ``t`` (the worker's wall-clock when it beat) is what lets a third-party observer date
    the beacon and so answer staleness for a run it did not launch (specs/observer-clock.md)."""

    step: Optional[int]
    consumed_seq: int
    t: float
    TOPIC: ClassVar[str] = Topic.LIFECYCLE_HEARTBEAT


@dataclass(frozen=True)
class Stopped:
    """The cooperative dying breath; its existence on the log = a clean, *resumable*
    halt (§7). ``completed=True`` is the worker's opt-in claim of intrinsic, permanent
    completion; otherwise the stop projects to ``preempted``. ``error`` is the failure
    diagnostic; a completed stop carries no error (enforced)."""

    completed: bool
    error: Optional[str]
    final_step: Optional[int]
    t: float                       # the worker's wall-clock at the dying breath (§ observer-clock)
    TOPIC: ClassVar[str] = Topic.LIFECYCLE_STOPPED

    def __post_init__(self) -> None:
        # completed ⟹ error is None: keeps the two content fields non-overlapping, so
        # `error is not None` ⟺ errored holds globally (mirrors Terminated's exited-XOR-killed).
        if self.completed and self.error is not None:
            raise ValueError("a completed stop cannot carry an error (completed ⟹ error is None)")


@dataclass(frozen=True)
class Nak:
    """A refused control request (§6), correlated by request_id."""

    reason: str  # "malformed" | "unsatisfiable" | "unsupported"
    message: str
    TOPIC: ClassVar[str] = Topic.LIFECYCLE_NAK


@dataclass(frozen=True)
class Launched:
    """Spawn-intent + the worker's liveness handle (§8). ``t`` is the launcher's
    wall-clock at spawn (§ observer-clock). Ordered before ``status`` so the required
    field precedes the defaulted one."""

    handle: str
    t: float
    status: str = "running"
    TOPIC: ClassVar[str] = Topic.LAUNCHER_LAUNCHED


@dataclass(frozen=True)
class Terminated:
    """The manner of death (§8): exited(exit_code) XOR killed(signal). ``t`` is the
    reaper's wall-clock when it observed the death (§ observer-clock)."""

    reason: str  # "exited" | "killed"
    exit_code: Optional[int]
    signal: Optional[int]
    t: float
    TOPIC: ClassVar[str] = Topic.LAUNCHER_TERMINATED

    def __post_init__(self) -> None:
        # Structural coupling: the schema enforces exited(exit_code) XOR
        # killed(signal) on the wire; mirror it here so an illegal object can't
        # exist in Python either. (Validation-only -- safe on a frozen dataclass.)
        if self.reason == "exited":
            if not isinstance(self.exit_code, int) or self.signal is not None:
                raise ValueError("exited requires a non-null exit_code and a null signal")
        elif self.reason == "killed":
            if not isinstance(self.signal, int) or self.exit_code is not None:
                raise ValueError("killed requires a non-null signal and a null exit_code")
        else:
            raise ValueError(f"reason must be 'exited' or 'killed', got {self.reason!r}")
