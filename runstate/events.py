"""events: typed worker → orchestrator events.

Opt-in module. Workers send Progress / Stopped via the helpers;
orchestrators receive raw dicts via Channel and parse them via parse().
Users may also send arbitrary dicts via channel.send() for non-protocol
messages — the helpers don't preclude that.

Event types (see protocol/messages-v0.1.schema.json for the wire format):
- Progress(step, metrics)
- Stopped(reason, metadata)
- Ack(of, command_id)

Ack is emitted by control.Checker.check() when it acts on a command;
workers typically don't construct Ack directly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Optional, Union

from .channel import Channel


@dataclass(frozen=True)
class Progress:
    """Periodic report of training state.

    step is optional for workers without a step concept (e.g., trial-based,
    wall-clock-based). metrics is a dict of numeric values keyed by name.
    """
    metrics: dict
    step: Optional[int] = None
    type: Literal["Progress"] = field(default="Progress", init=False)


@dataclass(frozen=True)
class Stopped:
    """Worker's self-described exit notification.

    Sent immediately before the worker exits. reason is a free-form
    string; common values: "natural", "preempted", "diverged",
    "nan_detected", "patience_triggered", "oom".
    """
    reason: str
    metadata: Optional[dict] = None
    type: Literal["Stopped"] = field(default="Stopped", init=False)


@dataclass(frozen=True)
class Ack:
    """Worker acknowledgment that a command was received and acted on.

    Auto-sent by control.Checker.check() when it returns a non-None
    command. Carries the original command's command_id so the
    orchestrator can match acks to sent commands.
    """
    of: str           # the type field of the acknowledged command
    command_id: str
    type: Literal["Ack"] = field(default="Ack", init=False)


Event = Union[Progress, Stopped, Ack]


# ----- helpers -----

def send(channel: Channel, event: Event) -> None:
    """Serialize a typed Event and send it."""
    channel.send(_to_wire(event))


def progress(
    channel: Channel,
    *,
    metrics: dict,
    step: Optional[int] = None,
) -> None:
    """Convenience: construct and send a Progress event."""
    send(channel, Progress(metrics=metrics, step=step))


def stopped(
    channel: Channel,
    *,
    reason: str,
    metadata: Optional[dict] = None,
) -> None:
    """Convenience: construct and send a Stopped event.

    Call immediately before exiting.
    """
    send(channel, Stopped(reason=reason, metadata=metadata))


def parse(msg: dict) -> Optional[Event]:
    """Parse a raw dict into a typed Event, or None if not recognized.

    Returns None for any dict that isn't a protocol-conformant event.
    Use channel.recv() and channel.send() directly for non-protocol
    dicts.
    """
    match msg.get("type"):
        case "Progress":
            if "metrics" not in msg:
                return None
            return Progress(metrics=msg["metrics"], step=msg.get("step"))
        case "Stopped":
            if "reason" not in msg:
                return None
            return Stopped(reason=msg["reason"], metadata=msg.get("metadata"))
        case "Ack":
            if "of" not in msg or "command_id" not in msg:
                return None
            return Ack(of=msg["of"], command_id=msg["command_id"])
        case _:
            return None


# ----- internals -----

def _to_wire(event: Event) -> dict:
    """Convert a typed Event to its wire-format dict.

    Equivalent to dataclasses.asdict(event), with the `type` field
    always included (dataclasses.asdict respects field(init=False) and
    includes it). We canonicalize None values too: per the schema,
    optional fields like step / metadata may be absent or null; we
    include them explicitly to keep wire format predictable.
    """
    return asdict(event)
