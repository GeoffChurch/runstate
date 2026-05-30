"""control: typed orchestrator → worker commands and worker-side Checker.

Opt-in module. Orchestrators send commands via the send helpers;
workers consume them via a Checker bound to their Channel.

The cooperative-preempt discipline lives here: Checker.check() drains
incoming commands, tracks pending deferred preempts (StopAtStep that
hasn't fired yet), and auto-sends Ack when it acts on a command.

Ack rule (see protocol/spec.md): only the command whose effect the
worker ACTUALLY ACTED ON gets ack'd. Superseded or subsumed pending
commands are dropped without Ack.
"""

from __future__ import annotations

import uuid
import weakref
from dataclasses import asdict, dataclass, field
from typing import Literal, Optional, Union

from .channel import Channel


def _gen_id() -> str:
    """Generate a 48-bit hex command_id."""
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class StopNow:
    """Ask the worker to stop ASAP at its next safe point."""
    command_id: str = field(default_factory=_gen_id)
    type: Literal["StopNow"] = field(default="StopNow", init=False)


@dataclass(frozen=True)
class StopAtStep:
    """Ask the worker to stop when its current step reaches `at`.

    Deferred preempt: the worker's Checker evaluates this against
    current_step on each check() call. Fires when current_step >= at.
    """
    at: int
    command_id: str = field(default_factory=_gen_id)
    type: Literal["StopAtStep"] = field(default="StopAtStep", init=False)


Command = Union[StopNow, StopAtStep]


# ----- orchestrator-side helpers -----

def send(channel: Channel, command: Command) -> str:
    """Serialize a typed Command and send it. Returns the command_id."""
    channel.send(asdict(command))
    return command.command_id


def send_stop(channel: Channel) -> str:
    """Convenience: send StopNow. Returns the command_id."""
    return send(channel, StopNow())


def send_stop_at_step(channel: Channel, step: int) -> str:
    """Convenience: send StopAtStep(at=step). Returns the command_id."""
    return send(channel, StopAtStep(at=step))


def parse(msg: dict) -> Optional[Command]:
    """Parse a raw dict into a typed Command, or None if not recognized."""
    match msg.get("type"):
        case "StopNow":
            cmd_id = msg.get("command_id")
            if cmd_id is None:
                return None
            return StopNow(command_id=cmd_id)
        case "StopAtStep":
            cmd_id = msg.get("command_id")
            at = msg.get("at")
            if cmd_id is None or at is None:
                return None
            return StopAtStep(at=at, command_id=cmd_id)
        case _:
            return None


# ----- worker-side: Checker -----

class Checker:
    """Worker-side helper that drains commands and tracks deferred preempt.

    Wraps a Channel and provides check() that:
    - drains all pending messages from the orchestrator
    - returns the active Command (if any) per the Ack rule
    - auto-emits Ack for commands it acts on
    - holds pending StopAtStep across calls until its trigger fires
      or it gets superseded/subsumed

    State lives on the Checker instance — not monkey-patched onto the
    Channel. Two Checkers wrapping the same Channel have independent
    pending state.
    """

    def __init__(self, channel: Channel):
        self._channel = channel
        self._pending: Optional[StopAtStep] = None  # held StopAtStep

    def check(self, *, current_step: Optional[int] = None) -> Optional[Command]:
        """Drain pending messages; return active Command if any.

        Behavior (see protocol/spec.md for the canonical rules):

        - StopNow → returned immediately; emits Ack.
          Any pending StopAtStep is subsumed (dropped without Ack).

        - StopAtStep(at=N):
          - if current_step is not None and current_step >= N:
              fire immediately → return it; emit Ack.
              Any other pending StopAtStep is superseded (dropped without Ack).
          - else: hold internally. If a NEW StopAtStep arrives later,
              it supersedes the old one (the old one is dropped without Ack).
              If current_step crosses the held one's `at` on a future
              check(), it fires and is ack'd then.

        - Non-Command dicts: ignored by this helper. Read them via
          channel.recv() directly if you need them.
        """
        # First, drain everything available without blocking. This lets
        # us see all pending commands so we can apply the supersede /
        # subsume rules correctly.
        incoming: list[Command] = []
        while True:
            msg = self._channel.recv(timeout=0)
            if msg is None:
                break
            cmd = parse(msg)
            if cmd is None:
                continue  # non-protocol dict; ignore
            incoming.append(cmd)

        # Now process the drained commands in arrival order, applying rules.
        # We process them in order so that "latest StopAtStep supersedes
        # earlier StopAtStep" is correctly evaluated.
        result: Optional[Command] = None

        for cmd in incoming:
            if isinstance(cmd, StopNow):
                # Subsume any pending StopAtStep (drop without Ack).
                self._pending = None
                # StopNow fires immediately.
                result = cmd
                # Continue processing — but anything further would also
                # be subsumed by StopNow, so we just break out of any
                # further fire considerations.
                # Note: there's no realistic case where additional
                # commands after StopNow matter; the worker is going to
                # exit. But for cleanliness:
                break
            elif isinstance(cmd, StopAtStep):
                # Supersede any pending; new one takes its place.
                # (Old one is dropped without Ack — that's the rule.)
                # Check if the new one fires immediately:
                if current_step is not None and current_step >= cmd.at:
                    # Fires now. Any held pending is dropped without Ack.
                    self._pending = None
                    result = cmd
                    # If there are further commands, continue — they may
                    # supersede us. We'd then drop ourselves without Ack
                    # and let the next one win.
                else:
                    # Hold. (Supersedes any previously-held without Ack.)
                    self._pending = cmd
                    result = None  # clear any earlier "fired" StopAtStep

        # If nothing in this drain fired, check whether the held one
        # has reached its trigger.
        if result is None and self._pending is not None:
            if current_step is not None and current_step >= self._pending.at:
                result = self._pending
                self._pending = None

        # Emit Ack for the command we acted on (if any).
        if result is not None:
            self._channel.send({
                "type": "Ack",
                "of": result.type,
                "command_id": result.command_id,
            })

        return result


# ----- functional convenience: cached Checker per Channel -----

# Per-Channel Checker cache. WeakKeyDictionary holds strong references
# to Checkers but weak references to Channels — entries disappear when
# the Channel is garbage-collected, but Checkers persist as long as
# their Channel is alive.
#
# Multi-Checker callers who want isolated state should construct
# explicit Checker(ch) instances instead.
_checker_cache: "weakref.WeakKeyDictionary[Channel, Checker]" = weakref.WeakKeyDictionary()


def check(channel: Channel, *, current_step: Optional[int] = None) -> Optional[Command]:
    """Functional convenience: equivalent to Checker(channel).check(...).

    Caches a Checker per Channel so that repeated calls on the same
    Channel share deferred state. To get isolated state, construct an
    explicit Checker instead.
    """
    checker = _checker_cache.get(channel)
    if checker is None:
        checker = Checker(channel)
        _checker_cache[channel] = checker
    return checker.check(current_step=current_step)
