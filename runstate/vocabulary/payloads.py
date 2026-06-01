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
from typing import Any, ClassVar, Optional


@dataclass(frozen=True)
class Value:
    """A worker's current value for ``name``, sampled per a subscription."""

    value: Any
    step: Optional[int]  # present-nullable: null when the worker is stepless
    t: Optional[float]  # seconds since worker birth (the real-time axis); null = unstamped
    TOPIC: ClassVar[str] = "value"


@dataclass(frozen=True)
class Started:
    """Pushed on attach; the worker self-reports its liveness handle (§8)."""

    handle: str
    hostname: Optional[str]
    attached_at: Optional[float]
    TOPIC: ClassVar[str] = "lifecycle.started"


@dataclass(frozen=True)
class Heartbeat:
    """Tick-driven liveness beacon (§7): progress + the consumption watermark."""

    step: Optional[int]
    consumed_seq: int
    TOPIC: ClassVar[str] = "lifecycle.heartbeat"


@dataclass(frozen=True)
class Stopped:
    """The cooperative dying breath; its existence on the log = clean finish (§7)."""

    reason: str
    error: Optional[str]
    final_step: Optional[int]
    TOPIC: ClassVar[str] = "lifecycle.stopped"


@dataclass(frozen=True)
class Nak:
    """A refused control request (§6), correlated by request_id."""

    reason: str  # "malformed" | "unsatisfiable" | "unsupported"
    message: str
    TOPIC: ClassVar[str] = "lifecycle.nak"


@dataclass(frozen=True)
class Launched:
    """Spawn-intent + the worker's liveness handle (§8)."""

    handle: str
    status: str = "running"
    TOPIC: ClassVar[str] = "launcher.launched"


@dataclass(frozen=True)
class Terminated:
    """The manner of death (§8): exited(exit_code) XOR killed(signal)."""

    reason: str  # "exited" | "killed"
    exit_code: Optional[int]
    signal: Optional[int]
    TOPIC: ClassVar[str] = "launcher.terminated"

    def __post_init__(self):
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
